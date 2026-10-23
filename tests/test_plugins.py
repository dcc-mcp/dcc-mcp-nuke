from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml
from jsonschema import validate

from dcc_mcp_nuke.plugins import inspect_plugin_environment, plugin_class_names

ROOT = Path(__file__).parents[1]
SKILL_ROOT = ROOT / "src" / "dcc_mcp_nuke" / "skills" / "nuke-plugins"


class FakeNuke:
    ALL = 1
    REGISTERED = 2
    NUKE_VERSION_STRING = "16.1v2"

    def __init__(self, *, paths=(), available=(), loaded=(), registered=()):
        self.paths = list(paths)
        self.available = list(available)
        self.loaded = list(loaded)
        self.registered = list(registered)
        self.calls = []

    def pluginPath(self):
        self.calls.append("paths")
        return self.paths

    def plugins(self, flags=0):
        self.calls.append(flags)
        return {0: self.loaded, self.ALL: self.available, self.REGISTERED: self.registered}[flags]

    def pluginAddPath(self, _path):
        pytest.fail("inventory must not activate paths")

    def load(self, _path):
        pytest.fail("inventory must not execute plug-ins")

    def createNode(self, _name, **_kwargs):
        pytest.fail("inventory must not create nodes")


def _file(root, name, content=b"metadata-only asset"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_inventory_distinguishes_registered_candidates_and_loaded_files(tmp_path):
    gizmo = _file(tmp_path, "Erode_Fine.gizmo")
    native = _file(tmp_path, "Unloaded.dll")
    nuke = FakeNuke(
        paths=[str(tmp_path)],
        available=[str(native), str(gizmo)],
        loaded=[str(gizmo)],
        registered=[str(gizmo), "Erode_Fine", "OFXcom.vendor.Bundle.Class"],
    )
    result = inspect_plugin_environment(
        nuke, required_node_classes=["Erode_Fine", "Unloaded", "Missing", "OFXcom.vendor.Bundle.Class"]
    )
    states = {item["node_class"]: item["status"] for item in result["requirements"]}
    assert states == {
        "Erode_Fine": "registered",
        "Unloaded": "discoverable_candidate",
        "Missing": "not_registered",
        "OFXcom.vendor.Bundle.Class": "registered",
    }
    assert result["registered_node_classes"] == ["Erode_Fine", "OFXcom.vendor.Bundle.Class"]
    assets = {Path(item["path"]).name: item for item in result["available_plugins"]}
    assert assets["Erode_Fine.gizmo"]["loaded"] is True
    assert assets["Unloaded.dll"]["loaded"] is False
    assert assets["Unloaded.dll"]["kind"] == "native"
    assert all(item["runtime_validation"] == "not_run" for item in result["requirements"])
    assert nuke.calls == ["paths", FakeNuke.ALL, 0, FakeNuke.REGISTERED]


def test_task_roots_scan_assets_without_executing_startup_or_changing_paths(tmp_path):
    marker = tmp_path / "must-not-exist"
    _file(tmp_path, "init.py", f"open({str(marker)!r}, 'w').write('executed')".encode())
    gizmo = _file(tmp_path, "category/ThirdParty.gizmo")
    _file(tmp_path, "category/README.txt")
    nuke = FakeNuke(paths=[], loaded=[str(gizmo)])
    result = inspect_plugin_environment(nuke, task_roots=[str(tmp_path)], required_node_classes=["ThirdParty"])
    assert not marker.exists()
    assert {Path(asset["path"]).name for asset in result["task_assets"]} == {"init.py", "ThirdParty.gizmo"}
    assert result["task_roots"][0]["active_search_path"] is False
    assert next(asset for asset in result["task_assets"] if asset["path"] == str(gizmo))["loaded"] is True
    assert result["requirements"][0]["status"] == "discoverable_candidate"
    assert nuke.paths == []
    assert result["loaded_plugins"][0]["path"] == str(gizmo)
    assert result["available_plugins"] == []


def test_noncommercial_gizmo_is_a_file_candidate_not_an_exact_registered_class(tmp_path):
    gizmo = _file(tmp_path, "NonCommercial.gznc")
    result = inspect_plugin_environment(
        FakeNuke(available=[str(gizmo)], registered=[str(gizmo)]), required_node_classes=["NonCommercial"]
    )
    assert result["requirements"][0]["status"] == "discoverable_candidate"
    assert result["registered_node_classes"] == []
    assert result["available_plugins"][0]["kind"] == "gizmo_noncommercial"
    assert "NonCommercial" in plugin_class_names([str(gizmo)])


def test_task_roots_are_deduplicated_and_active_path_order_is_preserved(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    _file(tmp_path, "Some.gizmo")
    nuke = FakeNuke(paths=[str(other), str(tmp_path)])
    result = inspect_plugin_environment(nuke, task_roots=[str(tmp_path), str(tmp_path)])
    assert [item["path"] for item in result["plugin_paths"]] == [str(other), str(tmp_path)]
    assert [item["order"] for item in result["plugin_paths"]] == [0, 1]
    assert len(result["task_roots"]) == 1
    assert result["task_roots"][0]["active_search_path"] is True


def test_filters_and_output_caps_do_not_change_requirement_or_search_path_checks(tmp_path):
    paths = [str(tmp_path / "first"), str(tmp_path / "second")]
    available = [str(_file(tmp_path, f"Erode{index}.gizmo")) for index in range(3)]
    nuke = FakeNuke(paths=paths, available=available, registered=["Unrelated", "Erode0"])
    result = inspect_plugin_environment(
        nuke, name_filter="ERODE", max_items=1, required_node_classes=["Unrelated", "Erode2"]
    )
    assert len(result["available_plugins"]) == 1
    assert result["available_plugins_count"] == 3
    assert result["available_plugins_truncated"] is True
    assert result["plugin_paths"][0]["path"] == paths[0]
    assert result["plugin_paths_count"] == 2
    assert result["plugin_paths_truncated"] is True
    assert result["requirements"][1]["status"] == "registered"


def test_probe_failures_are_unverified_and_do_not_expose_host_details():
    nuke = FakeNuke()

    def fail(*_args):
        raise OSError("private/plugin/license/detail")

    nuke.plugins = fail
    nuke.pluginPath = fail
    result = inspect_plugin_environment(nuke, required_node_classes=["Required"])
    assert result["requirements"][0]["status"] == "unverified"
    assert len(result["issues"]) == 4
    assert "private" not in str(result)
    assert not any(probe["available"] for probe in result["probes"].values())


def test_host_inventory_scan_cap_is_explicit_and_never_proves_absence():
    nuke = FakeNuke(available=["First.dll", "Second.dll", "Third.dll"])
    result = inspect_plugin_environment(nuke, max_scan_entries=1, required_node_classes=["Third"])
    assert result["probes"]["available_plugins"] == {"available": True, "count": 3, "truncated": True}
    assert result["requirements"][0]["status"] == "unverified"


def test_local_scan_limit_marks_unknown_requirements_unverified(tmp_path):
    for index in range(4):
        _file(tmp_path, f"Plugin{index}.gizmo")
    result = inspect_plugin_environment(
        FakeNuke(), task_roots=[str(tmp_path)], max_scan_entries=2, required_node_classes=["NotSeen"]
    )
    assert result["task_scan_incomplete"] is True
    assert result["task_roots"][0]["entries_scanned"] == 2
    assert result["requirements"][0]["status"] == "unverified"
    assert result["issues"] == [{"code": "task_root_scan_incomplete"}]


def test_directory_depth_is_bounded_and_ofx_bundle_is_one_asset(tmp_path):
    bundle = tmp_path / "Vendor.ofx.bundle"
    _file(bundle, "Contents/Win64/plugin.ofx")
    _file(tmp_path, "a/b/c/d/e/TooDeep.gizmo")
    result = inspect_plugin_environment(FakeNuke(), task_roots=[str(tmp_path)])
    assert result["task_scan_incomplete"] is True
    assert [asset["path"] for asset in result["task_assets"]] == [str(bundle)]
    assert result["task_assets"][0]["kind"] == "ofx_bundle"
    assert result["task_assets"][0]["size_bytes"] is None


def test_task_scan_does_not_follow_links_outside_the_named_root(tmp_path):
    root = tmp_path / "task"
    root.mkdir()
    external = _file(tmp_path, "external/NotTaskOwned.gizmo")
    try:
        (root / "linked").symlink_to(external.parent, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    result = inspect_plugin_environment(FakeNuke(), task_roots=[str(root)])
    assert result["task_assets"] == []


def test_missing_task_root_is_reported_without_creating_it(tmp_path):
    missing = tmp_path / "missing"
    result = inspect_plugin_environment(FakeNuke(), task_roots=[str(missing)])
    assert result["task_roots"][0]["exists"] is False
    assert not missing.exists()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_items": True},
        {"max_scan_entries": 0},
        {"task_roots": ["relative"]},
        {"task_roots": "not-a-list"},
        {"required_node_classes": ["Bad;exec"]},
        {"required_node_classes": [1]},
        {"name_filter": "a\nb"},
        {"name_filter": "x" * 129},
    ],
)
def test_direct_callers_are_validated(kwargs):
    with pytest.raises(ValueError):
        inspect_plugin_environment(FakeNuke(), **kwargs)


@pytest.mark.parametrize("entries", [[None], "not-a-list", ["bad\nentry"]])
def test_malformed_host_inventory_is_unverified(entries):
    nuke = FakeNuke()
    nuke.plugins = lambda *_args: entries
    result = inspect_plugin_environment(nuke, required_node_classes=["Requested"])
    assert result["requirements"][0]["status"] == "unverified"


def test_shared_name_normalization_retains_original_health_semantics():
    assert plugin_class_names(["C:\\plugins\\Native.dll", "/plugins/Source.gizmo", "OFXcom.vendor.Class"]) == {
        "C:\\plugins\\Native.dll",
        "Native",
        "/plugins/Source.gizmo",
        "Source",
        "OFXcom.vendor.Class",
    }
    assert "OFXcom" not in plugin_class_names(["OFXcom.vendor.Class"])


def test_skill_contract_is_read_only_main_thread_and_result_matches_schema(tmp_path):
    from dcc_mcp_core import validate_skill

    report = validate_skill(str(SKILL_ROOT))
    assert report.is_clean, [issue.message for issue in report.issues]
    manifest = yaml.safe_load((SKILL_ROOT / "tools.yaml").read_text(encoding="utf-8"))
    tool = manifest["tools"][0]
    assert tool["read_only"] is True
    assert tool["affinity"] == "main"
    assert tool["enforce_thread_affinity"] is True
    result = inspect_plugin_environment(FakeNuke(), task_roots=[str(tmp_path)], required_node_classes=["Example"])
    validate(result, tool["output_schema"])


def test_route_uses_bounded_contract_and_rejects_invalid_inputs(monkeypatch):
    monkeypatch.setitem(sys.modules, "nuke", FakeNuke(registered=["Example"]))
    spec = importlib.util.spec_from_file_location(
        "plugins_route", SKILL_ROOT / "scripts" / "inspect_plugin_environment.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.main.__wrapped__(required_node_classes=["Example"])
    assert result["success"] is True
    assert result["context"]["requirements"][0]["status"] == "registered"
    assert module.main.__wrapped__(max_items=0)["success"] is False


def test_server_loads_plugin_inventory_on_its_main_thread_bridge(monkeypatch):
    from dcc_mcp_nuke.dispatcher import NukeDispatcher
    from dcc_mcp_nuke.server import NukeMcpServer

    monkeypatch.setattr(NukeDispatcher, "start", lambda _self: None)
    monkeypatch.setattr(NukeDispatcher, "stop", lambda _self: None)
    server = NukeMcpServer(port=0)
    try:
        server.register_builtin_actions()
        assert server.load_skill("nuke-plugins") is True
        action = next(
            item for item in server.list_actions() if item["name"] == "nuke_plugins__inspect_plugin_environment"
        )
        assert action["thread_affinity"] == "main"
        assert action["enforce_thread_affinity"] is True
    finally:
        server.stop()
