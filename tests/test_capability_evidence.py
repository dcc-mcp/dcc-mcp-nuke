import sys
from types import ModuleType, SimpleNamespace

from dcc_mcp_nuke.capability_evidence import runtime_evidence


def test_missing_modules_are_unknown_not_supported(monkeypatch):
    monkeypatch.delitem(sys.modules, "nuke", raising=False)
    monkeypatch.delitem(sys.modules, "hiero.core", raising=False)
    report = runtime_evidence()
    assert all(item["state"] == "not_loaded" for item in report["surfaces"].values())
    assert report["license_status"] == "unknown"
    assert report["native_verification"] == "not_performed"
    assert report["authorizes_execution"] is False


def test_observation_does_not_invoke_host_api_or_grant_license(monkeypatch):
    def forbidden():
        raise AssertionError("Host APIs must not be invoked")

    nuke = ModuleType("nuke")
    nuke.__dict__.update(createNode=forbidden, toNode=forbidden, execute=forbidden)
    hiero = ModuleType("hiero.core")
    hiero.projects = forbidden
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    monkeypatch.setitem(sys.modules, "hiero.core", hiero)
    report = runtime_evidence()
    assert all(item["state"] == "observed" for item in report["surfaces"].values())
    assert report["license_status"] == "unknown"
    assert report["authorizes_execution"] is False


def test_noncallable_and_raising_attributes_are_not_available(monkeypatch):
    class BrokenModule:
        def __getattr__(self, name):
            raise RuntimeError("host not ready")

    monkeypatch.setitem(sys.modules, "nuke", SimpleNamespace(createNode=None, toNode="not callable"))
    monkeypatch.setitem(sys.modules, "hiero.core", BrokenModule())
    report = runtime_evidence()
    assert all(item["state"] == "incomplete" for item in report["surfaces"].values())
    assert all(item["callable_attributes"] == [] for item in report["surfaces"].values())


def test_host_flavor_tool_keeps_legacy_fields_and_adds_evidence(monkeypatch):
    import runpy
    from pathlib import Path

    import yaml
    from jsonschema import validate

    monkeypatch.setitem(sys.modules, "nuke", SimpleNamespace(env={"studio": True}))
    monkeypatch.delitem(sys.modules, "hiero.core", raising=False)
    monkeypatch.delenv("DCC_MCP_NUKE_HOST_FLAVOR", raising=False)
    monkeypatch.setattr("dcc_mcp_nuke.host_flavor.importlib.util.find_spec", lambda name: None)
    root = Path(__file__).parents[1] / "src/dcc_mcp_nuke/skills/nuke-diagnostics"
    result = runpy.run_path(str(root / "scripts/host_flavor.py"))["main"]()
    assert result["success"] is True
    data = result["context"]
    assert "studio.conform" in data["capabilities"]
    assert data["capabilities_semantics"] == "declared_host_surface_not_implemented_or_verified_tools"
    assert data["runtime_evidence"]["surfaces"]["hiero.project_inspection"]["state"] == "not_loaded"
    manifest = yaml.safe_load((root / "tools.yaml").read_text())
    schema = next(item for item in manifest["tools"] if item["name"] == "host_flavor")["output_schema"]
    validate(data, schema)


def test_lazy_module_hooks_are_never_called(monkeypatch):
    calls = []

    class LazyModule(ModuleType):
        def __getattribute__(self, name):
            calls.append(("getattribute", name))
            return super().__getattribute__(name)

    module = LazyModule("hiero.core")

    def lazy(name):
        calls.append(("getattr", name))
        return lambda: None

    ModuleType.__getattribute__(module, "__dict__")["__getattr__"] = lazy
    monkeypatch.setitem(sys.modules, "hiero.core", module)
    report = runtime_evidence()
    assert calls == []
    probe = report["surfaces"]["hiero.project_inspection"]
    assert probe["state"] == "incomplete"
    assert probe["callable_attributes"] == []
    assert probe["unobserved_attributes"] == ["projects"]
