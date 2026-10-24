from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest
from dcc_mcp_core import materialize_script


def _fake_nuke() -> ModuleType:
    module = ModuleType("nuke")
    module.execution_marker = "not-run"
    return module


def _execute_python(**params):
    from dcc_mcp_nuke.scripting import execute_python

    return execute_python(**params)


def test_execute_python_runs_materialized_script_for_current_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("DCC_MCP_SCRIPT_MATERIALIZATION_ROOT", str(tmp_path))
    monkeypatch.delenv("DCC_MCP_NUKE_DISABLE_EXECUTE_PYTHON", raising=False)
    monkeypatch.delenv("DCC_MCP_NUKE_DISABLE_ARBITRARY_SCRIPT", raising=False)
    nuke = _fake_nuke()
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    descriptor = materialize_script(
        "nuke.execution_marker = 'ran'\nresult = {'node_count': 3}",
        dcc_type="nuke",
        instance_id=str(os.getpid()),
        session_id="test-session",
        root=tmp_path,
    )

    result = _execute_python(file_path=descriptor.file_path)

    assert result["success"] is True
    assert result["context"]["result"] == {"node_count": 3}
    assert result["context"]["materialized_script"]["instance_id"] == str(os.getpid())
    assert result["context"]["materialized_script"]["sha256"] == descriptor.sha256
    assert nuke.execution_marker == "ran"


@pytest.mark.parametrize("entrypoint", ["code", "file_path"])
@pytest.mark.parametrize("line_endings", [("\n", "\n"), ("\r\n", "\r\n"), ("\n", "\r\n"), ("\r", "\r")])
def test_execute_python_preserves_materialized_bytes_with_universal_newlines(
    tmp_path, monkeypatch, entrypoint, line_endings
):
    monkeypatch.setenv("DCC_MCP_SCRIPT_MATERIALIZATION_ROOT", str(tmp_path))
    monkeypatch.delenv("DCC_MCP_NUKE_DISABLE_EXECUTE_PYTHON", raising=False)
    monkeypatch.delenv("DCC_MCP_NUKE_DISABLE_ARBITRARY_SCRIPT", raising=False)
    nuke = _fake_nuke()
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    first, second = line_endings
    code = f"nuke.execution_marker = 'ran'{first}print('你好 ✓'){second}result = 'crlf ok'"
    if entrypoint == "file_path":
        descriptor = materialize_script(
            code,
            dcc_type="nuke",
            instance_id=str(os.getpid()),
            session_id="test-session",
            root=tmp_path,
        )
        params = {"file_path": descriptor.file_path}
    else:
        params = {"code": code}

    result = _execute_python(**params)

    assert result["success"] is True, result
    assert result["context"]["result"] == "crlf ok"
    assert result["context"]["stdout"] == "你好 ✓\n"
    assert nuke.execution_marker == "ran"
    metadata = result["context"]["materialized_script"]
    body = Path(metadata["file_path"]).read_bytes()
    assert body == code.encode("utf-8")
    assert metadata["sha256"] == hashlib.sha256(body).hexdigest()
    assert metadata["bytes"] == len(body)
    assert metadata["file_ref"]["digest"] == f"sha256:{metadata['sha256']}"


def test_execute_python_rejects_script_materialized_for_another_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("DCC_MCP_SCRIPT_MATERIALIZATION_ROOT", str(tmp_path))
    nuke = _fake_nuke()
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    descriptor = materialize_script(
        "nuke.execution_marker = 'wrong-instance'",
        dcc_type="nuke",
        instance_id="another-instance",
        session_id="test-session",
        root=tmp_path,
    )

    result = _execute_python(file_path=descriptor.file_path)

    assert result["success"] is False
    assert "instance" in result["error"].lower()
    assert nuke.execution_marker == "not-run"


def test_execute_python_rejects_tampered_materialized_content(tmp_path, monkeypatch):
    monkeypatch.setenv("DCC_MCP_SCRIPT_MATERIALIZATION_ROOT", str(tmp_path))
    nuke = _fake_nuke()
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    descriptor = materialize_script(
        "nuke.execution_marker = 'original'",
        dcc_type="nuke",
        instance_id=str(os.getpid()),
        session_id="test-session",
        root=tmp_path,
    )
    Path(descriptor.file_path).write_text(
        "nuke.execution_marker = 'tampered'",
        encoding="utf-8",
    )

    result = _execute_python(file_path=descriptor.file_path)

    assert result["success"] is False
    # Core words this integrity failure as a digest mismatch up to 0.20.24 and
    # as a content/metadata mismatch from 0.20.25 on. Assert the semantics of
    # the rejection rather than one Core release's wording; the property that
    # actually matters is that the tampered script never ran (below).
    error = result["error"].lower()
    assert "digest" in error or "does not match" in error
    assert nuke.execution_marker == "not-run"


@pytest.mark.parametrize("tamper", ["content", "newlines"])
def test_execute_python_rejects_file_changes_after_core_normalization(tmp_path, monkeypatch, tamper):
    from dcc_mcp_nuke import scripting

    monkeypatch.setenv("DCC_MCP_SCRIPT_MATERIALIZATION_ROOT", str(tmp_path))
    nuke = _fake_nuke()
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    original = scripting._normalize_execution_params

    def normalize_then_tamper(params):
        prepared = original(params)
        body = Path(prepared.file_path).read_bytes()
        if tamper == "newlines":
            changed = body.replace(b"\r\n", b"\n")
        else:
            changed = body.replace(b"original", b"tampered")
        assert changed != body
        Path(prepared.file_path).write_bytes(changed)
        return prepared

    monkeypatch.setattr(scripting, "_normalize_execution_params", normalize_then_tamper)

    result = _execute_python(code="nuke.execution_marker = 'original'\r\nresult = 'ok'")

    assert result["success"] is False
    assert result["message"] == "Python execution refused"
    assert "digest" in result["error"]
    assert nuke.execution_marker == "not-run"


def test_execute_python_rejects_code_different_from_the_verified_file(tmp_path, monkeypatch):
    from dcc_mcp_nuke import scripting

    monkeypatch.setenv("DCC_MCP_SCRIPT_MATERIALIZATION_ROOT", str(tmp_path))
    nuke = _fake_nuke()
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    original = scripting._normalize_execution_params

    def normalize_with_different_code(params):
        prepared = original(params)
        return replace(prepared, code="nuke.execution_marker = 'different-code'")

    monkeypatch.setattr(scripting, "_normalize_execution_params", normalize_with_different_code)

    result = _execute_python(code="nuke.execution_marker = 'original'\r\nresult = 'ok'")

    assert result["success"] is False
    assert "text does not match the execution code" in result["error"]
    assert nuke.execution_marker == "not-run"


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("sha256", "0" * 64, "digest"),
        ("bytes", 0, "byte length"),
        ("file_ref", {"digest": "sha256:" + "0" * 64}, "FileRef digest"),
        ("expires_at", "2000-01-01T00:00:00Z", "expired"),
    ],
)
def test_execute_python_keeps_materialized_integrity_and_expiry_checks(tmp_path, monkeypatch, field, value, error):
    from dcc_mcp_nuke import scripting

    monkeypatch.setenv("DCC_MCP_SCRIPT_MATERIALIZATION_ROOT", str(tmp_path))
    nuke = _fake_nuke()
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    original = scripting._normalize_execution_params

    def normalize_then_change_metadata(params):
        prepared = original(params)
        path = Path(prepared.file_path)
        metadata_path = path.with_name(path.name + ".meta.json")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata[field] = value
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        return prepared

    monkeypatch.setattr(scripting, "_normalize_execution_params", normalize_then_change_metadata)

    result = _execute_python(code="nuke.execution_marker = 'original'\r\nresult = 'ok'")

    assert result["success"] is False
    assert result["message"] == "Python execution refused"
    assert error in result["error"]
    assert nuke.execution_marker == "not-run"


def test_execute_python_contains_system_exit_from_materialized_script(tmp_path, monkeypatch):
    monkeypatch.setenv("DCC_MCP_SCRIPT_MATERIALIZATION_ROOT", str(tmp_path))
    monkeypatch.setitem(sys.modules, "nuke", _fake_nuke())
    descriptor = materialize_script(
        "raise SystemExit('stop')",
        dcc_type="nuke",
        instance_id=str(os.getpid()),
        session_id="test-session",
        root=tmp_path,
    )

    result = _execute_python(file_path=descriptor.file_path)

    assert result["success"] is False
    assert result["message"] == "Python execution failed"
    assert "SystemExit: stop" in result["error"]


def test_execute_python_rejects_untrusted_or_unreadable_paths(tmp_path, monkeypatch):
    root = tmp_path / "materialized"
    root.mkdir()
    monkeypatch.setenv("DCC_MCP_SCRIPT_MATERIALIZATION_ROOT", str(root))
    nuke = _fake_nuke()
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    outside = tmp_path / "outside.py"
    outside.write_text("nuke.execution_marker = 'outside'", encoding="utf-8")

    outside_result = _execute_python(file_path=str(outside))

    assert outside_result["success"] is False
    assert "trusted" in outside_result["error"].lower()
    assert nuke.execution_marker == "not-run"

    descriptor = materialize_script(
        "nuke.execution_marker = 'unreadable'",
        dcc_type="nuke",
        instance_id=str(os.getpid()),
        session_id="test-session",
        root=root,
    )
    script_path = Path(descriptor.file_path).resolve()
    original_read_text = Path.read_text

    def deny_script_read(path, *args, **kwargs):
        if path.resolve() == script_path:
            raise PermissionError("operator denied script read")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny_script_read)
    unreadable_result = _execute_python(file_path=descriptor.file_path)

    assert unreadable_result["success"] is False
    assert "denied" in unreadable_result["error"].lower()
    assert nuke.execution_marker == "not-run"


@pytest.mark.parametrize(
    "policy_env",
    [
        "DCC_MCP_NUKE_DISABLE_EXECUTE_PYTHON",
        "DCC_MCP_NUKE_DISABLE_ARBITRARY_SCRIPT",
    ],
)
def test_execute_python_kill_switch_refuses_before_materialization(tmp_path, monkeypatch, policy_env):
    monkeypatch.setenv("DCC_MCP_SCRIPT_MATERIALIZATION_ROOT", str(tmp_path))
    monkeypatch.setenv(policy_env, "true")
    nuke = _fake_nuke()
    monkeypatch.setitem(sys.modules, "nuke", nuke)

    result = _execute_python(code="nuke.execution_marker = 'ran'")

    assert result["success"] is False
    assert "disabled by operator policy" in result["message"]
    assert not list(tmp_path.rglob("*.py"))
    assert nuke.execution_marker == "not-run"
