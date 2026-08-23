"""Policy-bound Python execution for the bundled Nuke scripting skill."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from dcc_mcp_core import (
    default_script_materialization_root,
    normalize_file_backed_script_execution_params,
    sanitize_materialization_segment,
)
from dcc_mcp_core.skill import skill_error, skill_success

ENV_DISABLE_EXECUTE_PYTHON = "DCC_MCP_NUKE_DISABLE_EXECUTE_PYTHON"
ENV_DISABLE_ARBITRARY_SCRIPT = "DCC_MCP_NUKE_DISABLE_ARBITRARY_SCRIPT"


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _execution_disabled() -> bool:
    return _env_truthy(ENV_DISABLE_ARBITRARY_SCRIPT) or _env_truthy(ENV_DISABLE_EXECUTE_PYTHON)


def _normalize_execution_params(params: Mapping[str, Any]):
    """Use the live server bridge when available, with the Core helper fallback."""
    instance_id = str(os.getpid())
    from dcc_mcp_nuke.server import current_execution_bridge

    bridge = current_execution_bridge()
    if bridge is not None:
        return bridge.prepare_script_execution_params(
            params,
            dcc_type="nuke",
            instance_id=instance_id,
            session_id="nuke-scripting",
            policy="auto",
            language="python",
            suffix=".py",
        )
    return normalize_file_backed_script_execution_params(
        params,
        dcc_type="nuke",
        instance_id=instance_id,
        session_id="nuke-scripting",
        policy="auto",
        language="python",
        suffix=".py",
    )


def _parse_expiry(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("materialized script has an invalid expiry") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validate_materialized_file(file_path: str, code: str) -> dict[str, Any]:
    """Verify Core's sidecar, ownership, content, and canonical instance path."""
    root = default_script_materialization_root().resolve()
    instance_id = str(os.getpid())
    safe_instance = sanitize_materialization_segment(instance_id)
    expected_root = (root / "nuke" / "temp" / safe_instance).resolve()
    path = Path(file_path).resolve()
    try:
        path.relative_to(expected_root)
    except ValueError as exc:
        raise PermissionError("materialized script belongs to a different Nuke instance") from exc
    if path.suffix.lower() != ".py":
        raise PermissionError("materialized script must be a .py file")

    metadata_path = path.with_name(path.name + ".meta.json")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PermissionError("materialized script metadata is missing") from exc
    except (OSError, ValueError, TypeError) as exc:
        raise PermissionError(f"materialized script metadata is unreadable: {exc}") from exc
    if not isinstance(metadata, dict):
        raise PermissionError("materialized script metadata must be an object")

    if metadata.get("dcc_type") != "nuke":
        raise PermissionError("materialized script DCC does not match Nuke")
    if metadata.get("instance_id") != safe_instance:
        raise PermissionError("materialized script instance does not match this Nuke process")
    if str(metadata.get("language", "")).lower() not in {"python", "py"}:
        raise PermissionError("materialized script language must be Python")
    if str(metadata.get("suffix", "")).lower() != ".py":
        raise PermissionError("materialized script metadata suffix must be .py")
    try:
        metadata_file = Path(str(metadata["file_path"])).resolve()
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise PermissionError("materialized script metadata path is invalid") from exc
    if metadata_file != path:
        raise PermissionError("materialized script metadata path does not match the file")

    body = code.encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    if metadata.get("sha256") != digest:
        raise PermissionError("materialized script digest does not match its content")
    if metadata.get("bytes") != len(body):
        raise PermissionError("materialized script byte length does not match its content")
    file_ref = metadata.get("file_ref")
    if isinstance(file_ref, dict) and file_ref.get("digest") != f"sha256:{digest}":
        raise PermissionError("materialized script FileRef digest does not match its content")

    expires_at = _parse_expiry(metadata.get("expires_at"))
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise PermissionError("materialized script has expired")
    return metadata


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)
    return value


def execute_python(
    *,
    code: str | None = None,
    file_path: str | None = None,
    script_path: str | None = None,
    capture_output: bool = True,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Execute Python on Nuke's host thread after Core-backed materialization."""
    if _execution_disabled():
        return skill_error(
            "execute_python is disabled by operator policy",
            f"Unset {ENV_DISABLE_EXECUTE_PYTHON} or {ENV_DISABLE_ARBITRARY_SCRIPT} "
            "to re-enable arbitrary Python execution.",
        )
    if not isinstance(capture_output, bool):
        return skill_error("Python execution refused", "capture_output must be a boolean")
    if file_path and script_path and Path(file_path).expanduser() != Path(script_path).expanduser():
        return skill_error("Python execution refused", "file_path and script_path disagree")
    selected_path = file_path or script_path
    if selected_path and code is not None:
        return skill_error("Python execution refused", "pass code or file_path, not both")

    params: dict[str, Any] = {}
    if selected_path:
        params["file_path"] = selected_path
    elif code is not None:
        params["code"] = code
    try:
        normalized = _normalize_execution_params(params)
        if normalized.file_path is None:
            raise PermissionError("Core did not produce a file-backed script")
        metadata = _validate_materialized_file(normalized.file_path, normalized.code)
    except (OSError, TypeError, ValueError) as exc:
        return skill_error("Python execution refused", str(exc))

    import nuke  # Lazy import: requires a running Nuke process.

    stdout = io.StringIO()
    stderr = io.StringIO()
    namespace: dict[str, Any] = {
        "__file__": normalized.file_path,
        "__name__": "__dcc_mcp_nuke_exec__",
        "nuke": nuke,
    }
    try:
        with contextlib.ExitStack() as stack:
            if capture_output:
                stack.enter_context(contextlib.redirect_stdout(stdout))
                stack.enter_context(contextlib.redirect_stderr(stderr))
            exec(  # noqa: S102 - operator-controlled, kill-switched fallback tool.
                compile(normalized.code, normalized.file_path, "exec"),
                namespace,
                namespace,
            )
    except BaseException as exc:  # noqa: BLE001 - keep SystemExit inside the tool envelope.
        return skill_error(
            "Python execution failed",
            f"{type(exc).__name__}: {exc}",
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
            materialized_script=metadata,
        )

    return skill_success(
        "Python executed successfully",
        result=_json_safe(namespace.get("result")),
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
        materialized_script=metadata,
    )


__all__ = [
    "ENV_DISABLE_ARBITRARY_SCRIPT",
    "ENV_DISABLE_EXECUTE_PYTHON",
    "execute_python",
]
