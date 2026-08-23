from __future__ import annotations

from pathlib import Path

SKILLS_ROOT = Path(__file__).parents[1] / "src" / "dcc_mcp_nuke" / "skills"


def test_all_companion_tools_named_in_descriptions_are_registered():
    from tools.skill_companion_audit import audit_companion_references

    assert audit_companion_references(SKILLS_ROOT) == []


def test_companion_audit_rejects_an_unregistered_tool_reference(tmp_path):
    import yaml

    from tools.skill_companion_audit import audit_companion_references

    skill_root = tmp_path / "example"
    skill_root.mkdir()
    (skill_root / "tools.yaml").write_text(
        yaml.safe_dump(
            {
                "tools": [
                    {
                        "name": "producer",
                        "description": "Pass the result to the `missing_runner` tool.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    issues = audit_companion_references(tmp_path)

    assert "producer description references unregistered companion tool missing_runner" in issues
    assert "materialize_script description references unregistered companion tool execute_python" in issues


def test_nuke_scripting_skill_is_valid_and_registers_file_executor():
    import yaml
    from dcc_mcp_core import validate_skill

    skill_root = SKILLS_ROOT / "nuke-scripting"
    report = validate_skill(str(skill_root))
    assert report.is_clean, [issue.message for issue in report.issues]

    manifest = yaml.safe_load((skill_root / "tools.yaml").read_text(encoding="utf-8"))
    execute_python = next(tool for tool in manifest["tools"] if tool["name"] == "execute_python")
    properties = execute_python["input_schema"]["properties"]
    assert {"code", "file_path", "script_path"} <= set(properties)
    assert execute_python["affinity"] == "main"
    assert execute_python["annotations"]["destructive_hint"] is True


def test_server_loads_nuke_scripting_on_its_existing_main_thread_bridge(monkeypatch):
    from dcc_mcp_nuke.dispatcher import NukeDispatcher
    from dcc_mcp_nuke.server import NukeMcpServer

    monkeypatch.setattr(NukeDispatcher, "start", lambda _self: None)
    monkeypatch.setattr(NukeDispatcher, "stop", lambda _self: None)
    server = NukeMcpServer(port=0)
    try:
        server.register_builtin_actions()

        assert server.load_skill("nuke-scripting") is True
        action = next(item for item in server.list_actions() if item["name"] == "nuke_scripting__execute_python")
        assert action["thread_affinity"] == "main"
        assert action["enforce_thread_affinity"] is True
        assert server._execution_bridge is server._nuke_execution_bridge
        assert server._nuke_execution_bridge.script_materialization_policy == "auto"
    finally:
        server.stop()
