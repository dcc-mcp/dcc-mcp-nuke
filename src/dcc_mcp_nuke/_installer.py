"""Nuke-owned Install SOP lifecycle built on public Core primitives."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import dcc_mcp_core
from dcc_mcp_core import (
    inspect_install_root,
    probe_sidecar_tool,
    query_runtime_state,
    safe_remove_tree,
    safe_replace_tree,
)

from dcc_mcp_nuke.__version__ import __version__
from dcc_mcp_nuke._install_contract import (
    INSTALL_EXIT_INSTALL,
    INSTALL_EXIT_OK,
    INSTALL_EXIT_PREFLIGHT,
    INSTALL_EXIT_REQUIRES_RESTART,
    INSTALL_EXIT_VERIFY,
    INSTALL_SOP_SCHEMA_VERSION,
)

DCC_TYPE = "nuke"
COMMAND = "dcc-mcp-nuke"
MIN_CORE_VERSION = "0.20.8"
MIN_NUKE_VERSION = (14, 0)
_PROFILE_ENV = "DCC_MCP_NUKE_PROFILE"
_PYTHON_ENV = "DCC_MCP_INSTALL_PYTHON"
_PLUGIN_DIR = "dcc-mcp-nuke"
_MANAGED_START = "# DCC-MCP NUKE MANAGED START"
_MANAGED_END = "# DCC-MCP NUKE MANAGED END"
_READINESS_TOOL = "nuke_diagnostics__ping"
_HOST_VERSION = re.compile(r"Nuke\s*(?P<major>\d+)\.(?P<minor>\d+)(?:v(?P<release>\d+))?", re.IGNORECASE)
_OFFICIAL_PYTHON_BY_NUKE_MAJOR = {14: (3, 9), 15: (3, 10), 16: (3, 11), 17: (3, 13)}


@dataclass(frozen=True)
class InstallContext:
    host_path: Path
    host_version: str
    host_version_source: str
    embedded_python_version: str
    profile: Path
    profile_source: str
    python_path: Path
    python_source: str
    python_version: str
    python_root: Path
    core_version: str
    state: str
    receipt_path: Path
    receipt: Optional[dict[str, Any]]
    plugin_root: Path
    shared_init: Path
    shared_init_existed: bool
    managed_block: str
    bootstrap_log_dir: Path


@dataclass(frozen=True)
class LifecycleOutcome:
    result: dict[str, Any]
    exit_code: int


class LifecycleFailure(RuntimeError):
    def __init__(self, stage: str, message: str, exit_code: int = INSTALL_EXIT_PREFLIGHT) -> None:
        super().__init__(message)
        self.stage = stage
        self.exit_code = exit_code


def _version_tuple(value: object) -> tuple[int, ...]:
    match = re.search(r"\d+(?:\.\d+)+", str(value or ""))
    return tuple(int(part) for part in match.group(0).split(".")) if match else ()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LifecycleFailure("receipt", f"Install receipt is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise LifecycleFailure("receipt", "Install receipt root must be an object.")
    return payload


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(str(temporary), str(path))


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(str(temporary), str(path))


def _query_python(python_path: Path) -> dict[str, str]:
    script = (
        "import json,sys,sysconfig; "
        "import dcc_mcp_core,dcc_mcp_nuke as adapter; "
        "print(json.dumps({'python_version':'.'.join(map(str,sys.version_info[:3])),"
        "'python_root':sysconfig.get_path('purelib'),'core_version':dcc_mcp_core.__version__,"
        "'adapter_version':adapter.__version__,'executable':sys.executable}))"
    )
    try:
        completed = subprocess.run(
            [str(python_path), "-c", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LifecycleFailure("python", f"Target interpreter could not run: {exc}") from exc
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout).strip().splitlines()
        detail = details[-1] if details else f"exit {completed.returncode}"
        raise LifecycleFailure("python", f"Target interpreter import check failed: {detail}")
    try:
        result = json.loads(completed.stdout)
    except ValueError as exc:
        raise LifecycleFailure("python", "Target interpreter returned invalid metadata.") from exc
    if result.get("adapter_version") != __version__:
        raise LifecycleFailure(
            "python",
            f"Target interpreter has adapter {result.get('adapter_version')!r}; expected {__version__!r}.",
        )
    if _version_tuple(result.get("core_version")) < _version_tuple(MIN_CORE_VERSION):
        raise LifecycleFailure(
            "core_version",
            f"dcc-mcp-core>={MIN_CORE_VERSION} is required in the target interpreter.",
        )
    return {str(key): str(value) for key, value in result.items()}


def _host_version(path: Path) -> tuple[str, str]:
    matches = list(_HOST_VERSION.finditer(" ".join((str(path), path.parent.name, path.name))))
    if not matches:
        return "", "unavailable"
    match = max(matches, key=lambda item: item.group("release") is not None)
    release = f"v{match.group('release')}" if match.group("release") else ""
    return f"{match.group('major')}.{match.group('minor')}{release}", "path"


def _host_candidates(environ: Mapping[str, str]) -> Sequence[Path]:
    candidates: set[Path] = set()
    if os.name == "nt":
        for key in ("ProgramFiles", "ProgramW6432"):
            root = environ.get(key, "").strip()
            if root:
                candidates.update(Path(root).glob("Nuke*/Nuke*.exe"))
    elif sys.platform == "darwin":
        candidates.update(Path("/Applications").glob("Nuke*/Nuke*.app/Contents/MacOS/Nuke*"))
    else:
        candidates.update(Path("/usr/local").glob("Nuke*/Nuke*"))
        discovered = shutil.which("nuke")
        if discovered:
            candidates.add(Path(discovered))
    return tuple(sorted((path.resolve() for path in candidates if path.is_file()), key=str))


def _resolve_host(dcc_path: Optional[str], environ: Mapping[str, str]) -> Path:
    if dcc_path:
        candidate = Path(dcc_path).expanduser().resolve()
        if candidate.is_dir():
            nested = [path for path in candidate.glob("Nuke*") if path.is_file()]
            nested.extend(path for path in candidate.glob("Nuke*.app/Contents/MacOS/Nuke*") if path.is_file())
            if len(nested) == 1:
                candidate = nested[0]
        if candidate.is_file():
            return candidate
        raise LifecycleFailure("host", f"Nuke executable does not exist: {candidate}")
    candidates = _host_candidates(environ)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise LifecycleFailure("host", "Nuke was not found in a standard install location; pass --dcc-path.")
    raise LifecycleFailure("host", "Multiple Nuke installations were found; select one with --dcc-path.")


def _python_candidates(host: Path) -> Sequence[Path]:
    roots = {host.parent, *tuple(host.parents)[:3]}
    relative = (
        Path("python.exe"),
        Path("python3.exe"),
        Path("python"),
        Path("python3"),
        Path("python.app/Contents/MacOS/python"),
    )
    found = {root / item for root in roots for item in relative if (root / item).is_file()}
    return tuple(sorted((path.resolve() for path in found), key=str))


def _detect_embedded_python_version(host: Path) -> str:
    roots = {host.parent, *tuple(host.parents)[:3]}
    versions: set[str] = set()
    for root in roots:
        for lib in (root / "lib", root / "python" / "lib", root / "Contents" / "Frameworks"):
            if not lib.is_dir():
                continue
            for marker in lib.glob("python*"):
                match = re.search(r"python\D*(\d)(?:\D?)(\d{1,2})", marker.name, re.IGNORECASE)
                if match:
                    versions.add(f"{int(match.group(1))}.{int(match.group(2))}")
    if len(versions) > 1:
        raise LifecycleFailure("python_compatibility", "Nuke contains ambiguous embedded Python version markers.")
    if versions:
        return next(iter(versions))
    host_version, _source = _host_version(host)
    host_parts = _version_tuple(host_version)
    expected = _OFFICIAL_PYTHON_BY_NUKE_MAJOR.get(host_parts[0] if host_parts else 0)
    return ".".join(map(str, expected)) if expected else ""


def _resolve_python(
    python_path: Optional[str],
    host: Path,
    environ: Mapping[str, str],
) -> tuple[Path, str]:
    selected = python_path or environ.get(_PYTHON_ENV)
    if selected:
        source = "--python" if python_path else _PYTHON_ENV
        path = Path(selected).expanduser().resolve()
        if not path.is_file():
            raise LifecycleFailure("python", f"Target interpreter does not exist: {path}")
        return path, source
    candidates = _python_candidates(host)
    if len(candidates) == 1:
        return candidates[0], "host_install"
    if not candidates:
        raise LifecycleFailure(
            "python",
            "Nuke's embedded interpreter was not found; pass its exact executable with --python.",
        )
    raise LifecycleFailure("python", "Multiple Nuke Python executables were found; select one with --python.")


def _profile_path(environ: Mapping[str, str]) -> tuple[Path, str]:
    explicit = environ.get(_PROFILE_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve(), _PROFILE_ENV
    nuke_path = environ.get("NUKE_PATH", "").strip()
    if nuke_path:
        entries = [item.strip() for item in nuke_path.split(os.pathsep) if item.strip()]
        if entries:
            return Path(entries[0]).expanduser().resolve(), "NUKE_PATH[0]"
    return (Path.home() / ".nuke").resolve(), "default_user_profile"


def _managed_block(plugin_root: Path, log_dir: Path) -> str:
    return (
        f"{_MANAGED_START}\n"
        "from dcc_mcp_core import capture_bootstrap_errors as _dcc_mcp_capture_bootstrap_errors\n"
        "with _dcc_mcp_capture_bootstrap_errors(\n"
        f"    'nuke', adapter_version={__version__!r}, min_core_version={MIN_CORE_VERSION!r},\n"
        f"    phase='registration', log_dir={str(log_dir)!r}\n"
        "):\n"
        "    import nuke as _dcc_mcp_nuke_host\n"
        f"    _dcc_mcp_nuke_host.pluginAddPath({str(plugin_root)!r})\n"
        f"{_MANAGED_END}\n"
    )


def _replace_managed_block(content: str, block: str) -> str:
    start = content.find(_MANAGED_START)
    end = content.find(_MANAGED_END)
    if (start == -1) != (end == -1):
        raise LifecycleFailure("registration", "The shared Nuke init.py has a partial managed block.")
    if start != -1:
        after = end + len(_MANAGED_END)
        if content.find(_MANAGED_START, start + 1) != -1 or content.find(_MANAGED_END, after) != -1:
            raise LifecycleFailure("registration", "The shared Nuke init.py has multiple managed blocks.")
        if after < len(content) and content[after] == "\n":
            after += 1
        return content[:start] + block + content[after:]
    separator = "" if not content or content.endswith("\n") else "\n"
    return content + separator + block


def _remove_managed_block(content: str, expected: str) -> str:
    if expected not in content:
        raise LifecycleFailure(
            "registration",
            "The receipted managed block in the shared Nuke init.py was modified.",
            INSTALL_EXIT_INSTALL,
        )
    return content.replace(expected, "", 1)


def _installation_state(
    plugin_root: Path,
    shared_init: Path,
    receipt_path: Path,
    managed_block: str,
) -> tuple[str, Optional[dict[str, Any]]]:
    shared_content = shared_init.read_text(encoding="utf-8") if shared_init.is_file() else ""
    artifacts_exist = plugin_root.exists() or _MANAGED_START in shared_content or _MANAGED_END in shared_content
    if not receipt_path.is_file():
        return ("partial", None) if artifacts_exist else ("fresh", None)
    try:
        receipt = _load_json(receipt_path)
    except LifecycleFailure:
        return "partial", None
    if receipt.get("schema_version") != 1 or receipt.get("dcc_type") != DCC_TYPE:
        return "partial", receipt
    files = receipt.get("files")
    if not isinstance(files, list) or not files:
        return "partial", receipt
    intact = True
    for item in files:
        if not isinstance(item, dict):
            intact = False
            break
        path = Path(str(item.get("path", "")))
        if not path.is_file() or _hash_file(path) != item.get("sha256"):
            intact = False
            break
    registration = receipt.get("registration")
    if not isinstance(registration, dict) or registration.get("managed_block") != managed_block:
        return "partial", receipt
    if managed_block not in shared_content:
        intact = False
    if receipt.get("adapter_version") != __version__:
        return "upgrade", receipt
    return ("current" if intact else "repair"), receipt


def _resolve_context(
    dcc_path: Optional[str],
    python_path: Optional[str],
    environ: Mapping[str, str],
) -> InstallContext:
    host = _resolve_host(dcc_path, environ)
    host_version, host_version_source = _host_version(host)
    if not host_version or _version_tuple(host_version) < MIN_NUKE_VERSION:
        raise LifecycleFailure(
            "host_version",
            "Nuke 14.0 or newer is required and the selected version could not be verified.",
        )
    interpreter, python_source = _resolve_python(python_path, host, environ)
    python = _query_python(interpreter)
    embedded_python = _detect_embedded_python_version(host)
    if not embedded_python:
        raise LifecycleFailure(
            "python_compatibility",
            "Nuke's embedded Python version could not be verified from the selected installation.",
        )
    if _version_tuple(python["python_version"])[:2] != _version_tuple(embedded_python)[:2]:
        raise LifecycleFailure(
            "python_compatibility",
            f"Nuke requires Python {embedded_python}; selected {python['python_version']}.",
        )

    profile, profile_source = _profile_path(environ)
    receipt_path = profile / ".dcc-mcp" / "receipts" / "nuke.json"
    plugin_root = profile / _PLUGIN_DIR
    shared_init = profile / "init.py"
    bootstrap_log_dir = profile / ".dcc-mcp" / "logs"
    managed_block = _managed_block(plugin_root, bootstrap_log_dir)
    state, receipt = _installation_state(plugin_root, shared_init, receipt_path, managed_block)
    return InstallContext(
        host_path=host,
        host_version=host_version,
        host_version_source=host_version_source,
        embedded_python_version=embedded_python,
        profile=profile,
        profile_source=profile_source,
        python_path=interpreter,
        python_source=python_source,
        python_version=python["python_version"],
        python_root=Path(python["python_root"]).resolve(),
        core_version=python["core_version"],
        state=state,
        receipt_path=receipt_path,
        receipt=receipt,
        plugin_root=plugin_root,
        shared_init=shared_init,
        shared_init_existed=shared_init.is_file(),
        managed_block=managed_block,
        bootstrap_log_dir=bootstrap_log_dir,
    )


def _base_result(ctx: InstallContext, *, status: str) -> dict[str, Any]:
    return {
        "schema_version": INSTALL_SOP_SCHEMA_VERSION,
        "status": status,
        "dcc_type": DCC_TYPE,
        "adapter_version": __version__,
        "core_version": ctx.core_version,
        "steps": [],
        "next_steps": [],
        "receipt_path": str(ctx.receipt_path),
        "verify": {"directly_usable": False, "failure_stage": None, "failure_reason": None},
        "plan": {
            "host_path": str(ctx.host_path),
            "host_version": ctx.host_version,
            "host_version_source": ctx.host_version_source,
            "embedded_python_version": ctx.embedded_python_version,
            "profile_path": str(ctx.profile),
            "profile_source": ctx.profile_source,
            "plugin_root": str(ctx.plugin_root),
            "nuke_path": os.environ.get("NUKE_PATH"),
            "python": {
                "executable": str(ctx.python_path),
                "version": ctx.python_version,
                "site_packages": str(ctx.python_root),
                "source": ctx.python_source,
            },
            "state": ctx.state,
        },
        "install_state": ctx.state,
    }


def _command(ctx: InstallContext, verb: str, *, execute: bool = False) -> list[str]:
    command = [COMMAND, verb, "--json"]
    if execute:
        command.append("--yes")
    command.extend(["--dcc-path", str(ctx.host_path), "--python", str(ctx.python_path)])
    return command


def _plan(ctx: InstallContext, verb: str) -> LifecycleOutcome:
    result = _base_result(ctx, status="planned")
    if verb in {"install", "upgrade"}:
        result["steps"] = [
            {"id": "preflight", "status": "ok"},
            {"id": "install", "status": "planned"},
            {"id": "receipt", "status": "planned"},
            {"id": "verify", "status": "planned"},
        ]
        result["next_steps"] = [
            {
                "id": "execute",
                "description": f"Execute the validated Nuke {verb} plan.",
                "command": _command(ctx, verb, execute=True),
                "why": "Planning does not modify Nuke or the install receipt.",
            }
        ]
    else:
        result["steps"] = [
            {"id": "receipt", "status": "ok" if ctx.receipt_path.is_file() else "absent"},
            {"id": "uninstall", "status": "planned"},
        ]
        result["next_steps"] = [
            {
                "id": "execute_uninstall",
                "description": "Remove only the receipted Nuke files and managed startup block.",
                "command": _command(ctx, "uninstall", execute=True),
                "why": "Planning does not modify the Nuke profile.",
            }
        ]
    return LifecycleOutcome(result, INSTALL_EXIT_OK)


def _receipt(ctx: InstallContext, installed_at: float) -> dict[str, Any]:
    files = [ctx.plugin_root / "init.py", ctx.plugin_root / "menu.py"]
    previous = ctx.receipt or {}
    previous_registration = previous.get("registration") if isinstance(previous.get("registration"), dict) else {}
    return {
        "schema_version": 1,
        "dcc_type": DCC_TYPE,
        "adapter_version": __version__,
        "core_version": ctx.core_version,
        "host": {
            "path": str(ctx.host_path),
            "version": ctx.host_version,
            "embedded_python_version": ctx.embedded_python_version,
        },
        "python": {
            "path": str(ctx.python_path),
            "version": ctx.python_version,
            "site_packages": str(ctx.python_root),
        },
        "profile": {"path": str(ctx.profile), "source": ctx.profile_source},
        "files": [{"path": str(path), "sha256": _hash_file(path)} for path in files],
        "registration": {
            "path": str(ctx.shared_init),
            "managed_block": ctx.managed_block,
            "file_existed_before": previous_registration.get("file_existed_before", ctx.shared_init_existed),
        },
        "bootstrap_error_dir": str(ctx.bootstrap_log_dir),
        "installed_at": datetime.fromtimestamp(installed_at, timezone.utc).isoformat(),
        "installed_at_epoch": installed_at,
        "previous_adapter_version": previous.get("adapter_version"),
    }


def _readiness_next_steps(ctx: InstallContext) -> list[dict[str, Any]]:
    return [
        {
            "id": "launch_nuke",
            "description": "Launch the selected Nuke executable and wait for its startup hook.",
            "command": [str(ctx.host_path)],
            "why": "Direct usability requires one live Nuke main-thread readiness probe.",
        },
        {
            "id": "verify_nuke",
            "description": "Verify the adapter after Nuke finishes starting.",
            "command": _command(ctx, "verify"),
            "why": "A copied plugin is not directly usable until its typed ping succeeds.",
        },
    ]


def _safe_probe(mcp_url: str, timeout: float) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(mcp_url)
    try:
        loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        loopback = (parsed.hostname or "").lower() == "localhost"
    if parsed.scheme != "http" or not loopback:
        return {
            "success": False,
            "status": "probe_unsafe_url",
            "message": "Nuke readiness probes require a loopback HTTP registry URL.",
        }
    result = probe_sidecar_tool(mcp_url, _READINESS_TOOL, timeout_secs=timeout)
    if result.get("status") != "probe_http_error" or result.get("http_status") != 406:
        return result
    return _probe_streamable_http(mcp_url, timeout)


def _probe_streamable_http(mcp_url: str, timeout: float) -> dict[str, Any]:
    """Retry Core 0.20.8's typed probe with the Streamable HTTP Accept header."""
    request_id = "nuke-install-readiness"
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": _READINESS_TOOL, "arguments": {}},
    }
    request = urllib.request.Request(
        mcp_url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(0.1, timeout)) as response:
            response_body = response.read(1_048_577)
            status_code = int(getattr(response, "status", 200))
    except urllib.error.HTTPError as error:
        return {
            "success": False,
            "status": "probe_http_error",
            "message": "Nuke readiness probe returned an HTTP error.",
            "http_status": error.code,
        }
    except (OSError, ValueError) as error:
        return {
            "success": False,
            "status": "probe_unreachable",
            "message": "Nuke readiness probe could not reach the loopback sidecar.",
            "error": str(error),
        }
    if len(response_body) > 1_048_576:
        return {
            "success": False,
            "status": "probe_bad_response",
            "message": "Nuke readiness probe exceeded the bounded response size.",
        }
    text = response_body.decode("utf-8", errors="replace")
    if text.lstrip().startswith("data:"):
        data_lines = [line[5:].lstrip() for line in text.splitlines() if line.startswith("data:")]
        text = "\n".join(data_lines)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, dict):
        return {
            "success": False,
            "status": "probe_bad_response",
            "message": "Nuke readiness probe returned a non-JSON-RPC response.",
            "http_status": status_code,
        }
    if parsed.get("error"):
        return {
            "success": False,
            "status": "probe_failed",
            "message": str(parsed["error"]),
            "http_status": status_code,
        }
    result = parsed.get("result")
    if not isinstance(result, dict) or result.get("isError") is True or result.get("success") is False:
        return {
            "success": False,
            "status": "probe_failed",
            "message": "Nuke typed readiness probe failed.",
            "http_status": status_code,
        }
    return {
        "success": True,
        "status": "probe_ok",
        "message": "Nuke typed readiness probe succeeded.",
        "http_status": status_code,
        "result": result,
    }


def _verify(ctx: InstallContext, environ: Mapping[str, str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not ctx.receipt_path.is_file():
        return {
            "directly_usable": False,
            "failure_stage": "receipt",
            "failure_reason": "No Nuke install receipt exists.",
        }, []
    receipt = _load_json(ctx.receipt_path)
    for item in receipt.get("files", []):
        path = Path(str(item.get("path", "")))
        if not path.is_file() or _hash_file(path) != item.get("sha256"):
            return {
                "directly_usable": False,
                "failure_stage": "artifact",
                "failure_reason": f"Receipted Nuke adapter file is missing or changed: {path}",
            }, []
    shared = ctx.shared_init.read_text(encoding="utf-8") if ctx.shared_init.is_file() else ""
    if receipt.get("registration", {}).get("managed_block") not in shared:
        return {
            "directly_usable": False,
            "failure_stage": "registration",
            "failure_reason": "The receipted Nuke startup registration is absent or changed.",
        }, []
    try:
        _query_python(ctx.python_path)
    except LifecycleFailure as exc:
        return {"directly_usable": False, "failure_stage": "import", "failure_reason": str(exc)}, []

    installed_at = float(receipt.get("installed_at_epoch", 0.0))
    errors = (
        [path for path in ctx.bootstrap_log_dir.glob("*.host-errors.log") if path.stat().st_mtime >= installed_at]
        if ctx.bootstrap_log_dir.is_dir()
        else []
    )
    if errors:
        return {
            "directly_usable": False,
            "failure_stage": "bootstrap",
            "failure_reason": f"Nuke captured a bootstrap failure in {errors[-1]}",
        }, []

    runtime = query_runtime_state(environ.get("DCC_MCP_REGISTRY_DIR"), dcc_type=DCC_TYPE, include_dead=False)
    entries = [entry for entry in runtime.get("entries", []) if entry.get("mcp_url")]
    if len(entries) != 1:
        reason = "No live Nuke adapter is registered." if not entries else "Multiple live Nuke adapters are registered."
        return {
            "directly_usable": False,
            "failure_stage": "readiness",
            "failure_reason": reason,
            "probe_tool": _READINESS_TOOL,
        }, _readiness_next_steps(ctx)
    timeout = max(0.1, float(environ.get("DCC_MCP_INSTALL_VERIFY_TIMEOUT", "2.0")))
    probe = _safe_probe(str(entries[0]["mcp_url"]), timeout)
    if not probe.get("success"):
        reason = str(probe.get("message") or probe.get("reason") or probe.get("status") or "Nuke ping failed")
        return {
            "directly_usable": False,
            "failure_stage": "readiness",
            "failure_reason": reason,
            "probe_tool": _READINESS_TOOL,
        }, _readiness_next_steps(ctx)
    return {
        "directly_usable": True,
        "failure_stage": None,
        "failure_reason": None,
        "probe_tool": _READINESS_TOOL,
    }, []


def _restore_path(current: Path, backup: Path) -> None:
    if current.is_dir():
        safe_remove_tree(current)
    elif current.exists():
        current.unlink()
    if backup.exists():
        current.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(backup), str(current))


def _execute_install(ctx: InstallContext, environ: Mapping[str, str]) -> LifecycleOutcome:
    if ctx.state == "partial":
        raise LifecycleFailure(
            "partial",
            "Nuke adapter files or registration exist without a valid matching receipt.",
        )
    inspection = inspect_install_root(ctx.plugin_root)
    if inspection.get("requires_restart"):
        result = _base_result(ctx, status="requires_restart")
        result["steps"] = [{"id": "preflight", "status": "requires_restart"}]
        result["next_steps"] = [
            {
                "id": "retry_after_nuke_restart",
                "description": "Close the locking Nuke process and repeat the install.",
                "command": _command(ctx, "install", execute=True),
                "why": "Core found a loaded native artifact under the adapter install root.",
            }
        ]
        result["lock"] = inspection
        return LifecycleOutcome(result, INSTALL_EXIT_REQUIRES_RESTART)

    transaction = ctx.profile / ".dcc-mcp" / "staging" / uuid.uuid4().hex
    staged_plugin = transaction / "payload" / _PLUGIN_DIR
    backup_plugin = transaction / "backup" / _PLUGIN_DIR
    backup_receipt = transaction / "backup" / "nuke.json"
    source_plugin = Path(__file__).resolve().parent / "nuke_plugin"
    staged_plugin.mkdir(parents=True)
    for name in ("init.py", "menu.py"):
        shutil.copy2(str(source_plugin / name), str(staged_plugin / name))
    old_shared_exists = ctx.shared_init.is_file()
    old_shared = ctx.shared_init.read_text(encoding="utf-8") if old_shared_exists else ""
    new_shared = _replace_managed_block(old_shared, ctx.managed_block)
    installed_at = time.time()
    try:
        backup_plugin.parent.mkdir(parents=True, exist_ok=True)
        if ctx.plugin_root.exists():
            os.replace(str(ctx.plugin_root), str(backup_plugin))
        if ctx.receipt_path.is_file():
            backup_receipt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(ctx.receipt_path), str(backup_receipt))
        replaced = safe_replace_tree(staged_plugin, ctx.plugin_root)
        if not replaced.get("success"):
            exit_code = INSTALL_EXIT_REQUIRES_RESTART if replaced.get("requires_restart") else INSTALL_EXIT_INSTALL
            raise LifecycleFailure("install", str(replaced.get("message") or "Staged replace failed."), exit_code)
        _write_text_atomic(ctx.shared_init, new_shared)
        _write_json_atomic(ctx.receipt_path, _receipt(ctx, installed_at))
    except BaseException:
        _restore_path(ctx.plugin_root, backup_plugin)
        if old_shared_exists:
            _write_text_atomic(ctx.shared_init, old_shared)
        elif ctx.shared_init.exists():
            ctx.shared_init.unlink()
        if backup_receipt.exists():
            ctx.receipt_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(str(backup_receipt), str(ctx.receipt_path))
        elif ctx.receipt_path.exists():
            ctx.receipt_path.unlink()
        raise
    finally:
        safe_remove_tree(transaction)

    verify, next_steps = _verify(ctx, environ)
    result = _base_result(ctx, status="ok" if verify["directly_usable"] else "partial")
    result["steps"] = [
        {"id": "preflight", "status": "ok"},
        {"id": "install", "status": "ok"},
        {"id": "receipt", "status": "ok"},
        {"id": "verify", "status": "ok" if verify["directly_usable"] else "failed"},
    ]
    result["verify"] = verify
    result["next_steps"] = next_steps
    return LifecycleOutcome(result, INSTALL_EXIT_OK if verify["directly_usable"] else INSTALL_EXIT_VERIFY)


def _execute_uninstall(ctx: InstallContext) -> LifecycleOutcome:
    if not ctx.receipt_path.is_file():
        shared = ctx.shared_init.read_text(encoding="utf-8") if ctx.shared_init.is_file() else ""
        if ctx.plugin_root.exists() or _MANAGED_START in shared or _MANAGED_END in shared:
            raise LifecycleFailure("partial", "Unreceipted Nuke adapter state cannot be removed safely.")
        result = _base_result(ctx, status="ok")
        result["steps"] = [{"id": "uninstall", "status": "already_absent"}]
        return LifecycleOutcome(result, INSTALL_EXIT_OK)

    receipt = _load_json(ctx.receipt_path)
    expected = {(ctx.plugin_root / name).resolve() for name in ("init.py", "menu.py")}
    recorded = {Path(str(item.get("path", ""))).resolve() for item in receipt.get("files", [])}
    if recorded != expected:
        raise LifecycleFailure(
            "receipt",
            "The receipt does not own exactly the Nuke adapter plugin files.",
            INSTALL_EXIT_INSTALL,
        )
    for item in receipt["files"]:
        path = Path(str(item["path"]))
        if path.exists() and _hash_file(path) != item.get("sha256"):
            raise LifecycleFailure(
                "receipt",
                f"Receipted file was modified and will be preserved: {path}",
                INSTALL_EXIT_INSTALL,
            )
    registration = receipt.get("registration")
    if not isinstance(registration, dict) or not isinstance(registration.get("managed_block"), str):
        raise LifecycleFailure("receipt", "The receipt has no managed Nuke registration block.")
    shared_content = ctx.shared_init.read_text(encoding="utf-8") if ctx.shared_init.is_file() else ""
    remaining = _remove_managed_block(shared_content, registration["managed_block"])

    inspection = inspect_install_root(ctx.plugin_root)
    if inspection.get("requires_restart"):
        result = _base_result(ctx, status="requires_restart")
        result["steps"] = [{"id": "uninstall", "status": "requires_restart"}]
        result["next_steps"] = [
            {
                "id": "retry_uninstall",
                "description": "Close Nuke and repeat the receipt-driven uninstall.",
                "command": _command(ctx, "uninstall", execute=True),
                "why": "Core found a loaded native artifact under the receipted plugin root.",
            }
        ]
        result["lock"] = inspection
        return LifecycleOutcome(result, INSTALL_EXIT_REQUIRES_RESTART)

    transaction = ctx.profile / ".dcc-mcp" / "staging" / uuid.uuid4().hex
    backup_plugin = transaction / "backup" / _PLUGIN_DIR
    backup_shared = transaction / "backup" / "shared-init.py"
    backup_receipt = transaction / "backup" / "nuke.json"
    backup_plugin.parent.mkdir(parents=True, exist_ok=True)
    if ctx.plugin_root.is_dir():
        shutil.copytree(str(ctx.plugin_root), str(backup_plugin))
    if ctx.shared_init.is_file():
        shutil.copy2(str(ctx.shared_init), str(backup_shared))
    shutil.copy2(str(ctx.receipt_path), str(backup_receipt))

    removed = safe_remove_tree(ctx.plugin_root)
    if not removed.get("success"):
        result = _base_result(ctx, status="requires_restart" if removed.get("requires_restart") else "failed")
        result["steps"] = [{"id": "uninstall", "status": result["status"]}]
        result["next_steps"] = [
            {
                "id": "retry_uninstall",
                "description": "Close Nuke and repeat the receipt-driven uninstall.",
                "command": _command(ctx, "uninstall", execute=True),
                "why": str(removed.get("message") or "The receipted plugin could not be removed."),
            }
        ]
        exit_code = INSTALL_EXIT_REQUIRES_RESTART if removed.get("requires_restart") else INSTALL_EXIT_INSTALL
        safe_remove_tree(transaction)
        return LifecycleOutcome(result, exit_code)
    try:
        if remaining or registration.get("file_existed_before"):
            _write_text_atomic(ctx.shared_init, remaining)
        elif ctx.shared_init.exists():
            ctx.shared_init.unlink()
        ctx.receipt_path.unlink()
    except BaseException as error:
        if not ctx.plugin_root.exists() and backup_plugin.exists():
            restored = safe_replace_tree(backup_plugin, ctx.plugin_root)
            if not restored.get("success"):
                raise LifecycleFailure(
                    "rollback",
                    str(restored.get("message") or "Uninstall rollback could not restore the plugin."),
                    INSTALL_EXIT_INSTALL,
                ) from error
        if backup_shared.exists():
            ctx.shared_init.parent.mkdir(parents=True, exist_ok=True)
            os.replace(str(backup_shared), str(ctx.shared_init))
        elif ctx.shared_init.exists():
            ctx.shared_init.unlink()
        if backup_receipt.exists():
            ctx.receipt_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(str(backup_receipt), str(ctx.receipt_path))
        raise
    finally:
        safe_remove_tree(transaction)
    result = _base_result(ctx, status="ok")
    result["steps"] = [
        {"id": "receipt", "status": "consumed"},
        {"id": "uninstall", "status": "ok"},
    ]
    return LifecycleOutcome(result, INSTALL_EXIT_OK)


def _status(ctx: InstallContext) -> LifecycleOutcome:
    incomplete = ctx.state in {"partial", "repair"}
    result = _base_result(ctx, status="partial" if incomplete else "ok")
    result["steps"] = [
        {"id": "receipt", "status": "ok" if ctx.receipt_path.is_file() else "absent"},
        {"id": "artifacts", "status": ctx.state},
    ]
    return LifecycleOutcome(result, INSTALL_EXIT_PREFLIGHT if incomplete else INSTALL_EXIT_OK)


def _verify_outcome(ctx: InstallContext, environ: Mapping[str, str]) -> LifecycleOutcome:
    verify, next_steps = _verify(ctx, environ)
    result = _base_result(ctx, status="ok" if verify["directly_usable"] else "failed")
    result["steps"] = [{"id": "verify", "status": "ok" if verify["directly_usable"] else "failed"}]
    result["verify"] = verify
    result["next_steps"] = next_steps
    return LifecycleOutcome(result, INSTALL_EXIT_OK if verify["directly_usable"] else INSTALL_EXIT_VERIFY)


def _failure_result(
    dcc_path: Optional[str],
    python_path: Optional[str],
    environ: Mapping[str, str],
    failure: LifecycleFailure,
) -> LifecycleOutcome:
    profile, _source = _profile_path(environ)
    retry = [COMMAND, "status", "--json"]
    if dcc_path:
        retry.extend(["--dcc-path", dcc_path])
    if python_path or environ.get(_PYTHON_ENV):
        retry.extend(["--python", python_path or environ[_PYTHON_ENV]])
    result = {
        "schema_version": INSTALL_SOP_SCHEMA_VERSION,
        "status": "failed",
        "dcc_type": DCC_TYPE,
        "adapter_version": __version__,
        "core_version": str(getattr(dcc_mcp_core, "__version__", "unknown")),
        "steps": [{"id": "preflight", "status": "failed", "message": str(failure)}],
        "next_steps": [
            {
                "id": "retry_preflight",
                "description": "Repeat with the exact Nuke host and embedded interpreter.",
                "command": retry,
                "why": str(failure),
            }
        ],
        "receipt_path": str(profile / ".dcc-mcp" / "receipts" / "nuke.json"),
        "verify": {
            "directly_usable": False,
            "failure_stage": failure.stage,
            "failure_reason": str(failure),
        },
    }
    return LifecycleOutcome(result, failure.exit_code)


def run_lifecycle(
    verb: str,
    *,
    dcc_path: Optional[str],
    python_path: Optional[str],
    yes: bool,
    dry_run: bool,
    environ: Optional[Mapping[str, str]] = None,
) -> LifecycleOutcome:
    """Execute one public Nuke lifecycle verb."""
    resolved_environ = os.environ if environ is None else environ
    try:
        ctx = _resolve_context(dcc_path, python_path, resolved_environ)
        if verb == "status":
            return _status(ctx)
        if verb == "verify":
            return _verify_outcome(ctx, resolved_environ)
        if verb == "uninstall":
            return _plan(ctx, verb) if dry_run or not yes else _execute_uninstall(ctx)
        if verb in {"install", "upgrade"}:
            return _plan(ctx, verb) if dry_run or not yes else _execute_install(ctx, resolved_environ)
        raise LifecycleFailure("verb", f"Unsupported lifecycle verb: {verb}")
    except LifecycleFailure as exc:
        return _failure_result(dcc_path, python_path, resolved_environ, exc)
    except BaseException as exc:
        failure = LifecycleFailure("install", f"Lifecycle operation failed: {exc}", INSTALL_EXIT_INSTALL)
        return _failure_result(dcc_path, python_path, resolved_environ, failure)


__all__ = [
    "COMMAND",
    "DCC_TYPE",
    "MIN_CORE_VERSION",
    "MIN_NUKE_VERSION",
    "LifecycleOutcome",
    "run_lifecycle",
]
