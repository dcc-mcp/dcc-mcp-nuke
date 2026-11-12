"""The new diagnostics helpers must be discoverable and callable as tools.

Library helpers that no tool exposes are not agent-usable functionality. These
tests cover manifest registration, in-host server discovery and the script
entry points, without a live Nuke host or any network call.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml
from dcc_mcp_core import validate_skill
from jsonschema import validate

ROOT = Path(__file__).parents[1]
SKILL_ROOT = ROOT / "src" / "dcc_mcp_nuke" / "skills" / "nuke-diagnostics"
NEW_TOOLS = ("diagnose_dependencies", "describe_execution_sources")


def _manifest():
    return yaml.safe_load((SKILL_ROOT / "tools.yaml").read_text(encoding="utf-8"))


def _tool(name):
    return next(tool for tool in _manifest()["tools"] if tool["name"] == name)


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, SKILL_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_new_diagnostics_tools_are_declared_read_only_with_source_files(name):
    tool = _tool(name)
    assert tool["read_only"] is True
    assert tool["destructive"] is False
    assert tool["affinity"] == "main"
    assert (SKILL_ROOT / tool["source_file"]).is_file()
    assert tool["input_schema"]["additionalProperties"] is False
    assert "properties" in tool["output_schema"]


def test_diagnostics_skill_manifest_is_valid():
    report = validate_skill(str(SKILL_ROOT))
    assert report.is_clean, [issue.message for issue in report.issues]


def test_server_registers_the_new_diagnostics_actions(monkeypatch):
    from dcc_mcp_nuke.dispatcher import NukeDispatcher
    from dcc_mcp_nuke.server import NukeMcpServer

    monkeypatch.setattr(NukeDispatcher, "start", lambda _self: None)
    monkeypatch.setattr(NukeDispatcher, "stop", lambda _self: None)
    server = NukeMcpServer(port=0)
    try:
        server.register_builtin_actions()
        assert server.load_skill("nuke-diagnostics") is True
        names = {item["name"] for item in server.list_actions()}
        for name in NEW_TOOLS:
            assert f"nuke_diagnostics__{name}" in names
    finally:
        server.stop()


def test_diagnose_dependencies_route_is_callable_and_schema_valid(monkeypatch):
    host = ModuleType("nuke")
    host.NUKE_VERSION_STRING = "16.1v2"
    monkeypatch.setitem(sys.modules, "nuke", host)
    module = _load_script("diagnose_dependencies")

    result = module.main.__wrapped__(required_modules=["os"])

    assert result["success"] is True
    context = result["context"]
    assert context["modules"][0]["status"] in ("discoverable", "unknown", "not_found")
    assert context["module_probe"]["import_tested"] is False
    assert context["safety"]["module_code_executed"] is False
    validate(context, _tool("diagnose_dependencies")["output_schema"])
    assert module.main.__wrapped__(max_items=0)["success"] is False


def test_execution_sources_route_is_callable_and_stays_accurate():
    module = _load_script("describe_execution_sources")

    result = module.main.__wrapped__()

    assert result["success"] is True
    context = result["context"]
    external = context["external_services"]
    assert external["cloud_model_integration"] == "none"
    assert external["provider"] == "none"
    assert external["billing"] == "not_applicable"
    assert external["byo_api_key"] == "not_applicable"
    assert external["paid_calls"] == "none"
    assert external["paid_calls_added_by_this_change"] is False
    assert context["no_cloud_integration"] is True
    assert context["no_billing_estimates"] is True
    assert context["no_api_keys"] is True
    assert context["native_host_verification"] == "not_performed"
    validate(context, _tool("describe_execution_sources")["output_schema"])
