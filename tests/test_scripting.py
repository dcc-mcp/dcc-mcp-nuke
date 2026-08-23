from __future__ import annotations

import os
import sys
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
    assert "digest" in result["error"].lower()
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
