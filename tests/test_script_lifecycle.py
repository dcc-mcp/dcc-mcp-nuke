from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from dcc_mcp_nuke.script_lifecycle import (
    ScriptLifecycleRuntimeError,
    ScriptLifecycleValidationError,
    get_root_settings,
    new_script,
    paste_nodes,
    set_root_settings,
)

ROOT = Path(__file__).parents[1]
SKILL_ROOT = ROOT / "src" / "dcc_mcp_nuke" / "skills" / "nuke-script"


class FakeKnob:
    def __init__(self, value, values=None, *, reject=()):
        self.current = value
        self._values = values
        self.reject = set(reject)

    def value(self):
        return self.current

    def setValue(self, value):
        if value in self.reject:
            return
        self.current = value

    def values(self):
        if self._values is None:
            raise RuntimeError("not an enumeration knob")
        return list(self._values)


class FakeNode:
    def __init__(self, name, node_class):
        self._name = name
        self._class = node_class

    def name(self):
        return self._name

    def fullName(self):
        return self._name

    def Class(self):
        return self._class


class FakeRoot:
    def __init__(self, knobs, modified=False):
        self._knobs = dict(knobs)
        self._modified = modified

    def __getitem__(self, name):
        if name not in self._knobs:
            raise KeyError(name)
        return self._knobs[name]

    def modified(self):
        return self._modified


class FakeNuke:
    def __init__(
        self,
        root_knobs=None,
        nodes=(),
        *,
        modified=False,
        paste_result=None,
        paste_raises=False,
        paste_drops_existing=False,
    ):
        self._root = FakeRoot(root_knobs or {}, modified)
        self.nodes = list(nodes)
        self.cleared = False
        self.deleted = []
        self.pasted = []
        self.paste_result = list(paste_result or ())
        self.paste_raises = paste_raises
        self.paste_drops_existing = paste_drops_existing

    def root(self):
        return self._root

    def allNodes(self, recurseGroups=True):
        assert recurseGroups is True
        return list(self.nodes)

    def toNode(self, name):
        return next((node for node in self.nodes if node.name() == name), None)

    def scriptClear(self):
        self.cleared = True
        self.nodes = []

    def nodePaste(self, path):
        self.pasted.append(path)
        if self.paste_drops_existing:
            self.nodes = []
        for node in self.paste_result:
            self.nodes.append(node)
        if self.paste_raises:
            raise RuntimeError("paste failed")

    def delete(self, node):
        self.deleted.append(node)
        if node in self.nodes:
            self.nodes.remove(node)


def _root_knobs(first=1, last=100, fps=24.0, fmt="HD_1080", formats=("HD_1080", "HD_720", "UHD_4K")):
    return {
        "first_frame": FakeKnob(first),
        "last_frame": FakeKnob(last),
        "fps": FakeKnob(fps),
        "format": FakeKnob(fmt, values=list(formats)),
    }


def _load_route(name):
    path = SKILL_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"nuke_script_route_{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_get_root_settings_reports_frame_range_fps_and_enumerated_formats():
    nuke = FakeNuke(_root_knobs())

    result = get_root_settings(nuke)

    assert result["first_frame"] == 1
    assert result["last_frame"] == 100
    assert result["fps"] == 24.0
    assert result["format"] == "HD_1080"
    assert result["available_formats"] == ["HD_1080", "HD_720", "UHD_4K"]
    assert result["available_format_count"] == 3
    assert result["available_formats_truncated"] is False


def test_get_root_settings_bounds_format_enumeration():
    formats = [f"FMT{index:03d}" for index in range(40)]
    nuke = FakeNuke(_root_knobs(formats=formats))

    result = get_root_settings(nuke, max_formats=5)

    assert result["available_formats"] == formats[:5]
    assert result["available_format_count"] == 40
    assert result["available_formats_truncated"] is True


def test_set_root_settings_applies_frame_range_and_reads_back():
    knobs = _root_knobs()
    nuke = FakeNuke(knobs)

    result = set_root_settings(nuke, first_frame=10, last_frame=50, fps=25.0)

    assert result["first_frame"] == 10
    assert result["last_frame"] == 50
    assert result["fps"] == 25.0
    assert knobs["first_frame"].value() == 10
    assert knobs["last_frame"].value() == 50
    assert knobs["fps"].value() == 25.0


def test_set_root_settings_sets_format_from_the_enumerated_list():
    knobs = _root_knobs()
    nuke = FakeNuke(knobs)

    set_root_settings(nuke, format="UHD_4K")

    assert knobs["format"].value() == "UHD_4K"


def test_set_root_settings_rejects_empty_request():
    nuke = FakeNuke(_root_knobs())

    with pytest.raises(ScriptLifecycleValidationError):
        set_root_settings(nuke)


def test_set_root_settings_rejects_inverted_frame_range():
    knobs = _root_knobs()
    nuke = FakeNuke(knobs)

    with pytest.raises(ScriptLifecycleValidationError):
        set_root_settings(nuke, first_frame=200)

    assert knobs["first_frame"].value() == 1


def test_set_root_settings_rejects_unknown_format():
    knobs = _root_knobs()
    nuke = FakeNuke(knobs)

    with pytest.raises(ScriptLifecycleValidationError):
        set_root_settings(nuke, format="NOPE")

    assert knobs["format"].value() == "HD_1080"


def test_set_root_settings_rejects_non_positive_fps():
    nuke = FakeNuke(_root_knobs())

    with pytest.raises(ScriptLifecycleValidationError):
        set_root_settings(nuke, fps=0)

    with pytest.raises(ScriptLifecycleValidationError):
        set_root_settings(nuke, fps=-24)


def test_set_root_settings_rolls_back_on_readback_mismatch():
    knobs = _root_knobs()
    knobs["fps"] = FakeKnob(24.0, reject={25.0})
    nuke = FakeNuke(knobs)

    with pytest.raises(ScriptLifecycleRuntimeError):
        set_root_settings(nuke, fps=25.0)

    assert knobs["fps"].value() == 24.0


def test_new_script_guards_unsaved_changes_before_clearing():
    nuke = FakeNuke(_root_knobs(), nodes=[FakeNode("Read1", "Read")], modified=True)

    with pytest.raises(ScriptLifecycleValidationError):
        new_script(nuke)

    assert nuke.cleared is False
    assert [node.name() for node in nuke.nodes] == ["Read1"]


def test_new_script_clears_graph_when_discarding_unsaved_changes():
    nuke = FakeNuke(_root_knobs(), nodes=[FakeNode("Read1", "Read")], modified=True)

    result = new_script(nuke, discard_unsaved_changes=True)

    assert nuke.cleared is True
    assert result["node_count"] == 0
    assert nuke.nodes == []


def test_new_script_applies_root_settings_after_clearing():
    knobs = _root_knobs()
    nuke = FakeNuke(knobs, nodes=[FakeNode("Read1", "Read")])

    result = new_script(nuke, first_frame=1001, last_frame=1100, fps=23.976, format="UHD_4K")

    assert result["node_count"] == 0
    assert knobs["first_frame"].value() == 1001
    assert knobs["last_frame"].value() == 1100
    assert knobs["fps"].value() == 23.976
    assert knobs["format"].value() == "UHD_4K"


def test_new_script_requires_frame_bounds_together():
    nuke = FakeNuke(_root_knobs())

    with pytest.raises(ScriptLifecycleValidationError):
        new_script(nuke, first_frame=1)

    with pytest.raises(ScriptLifecycleValidationError):
        new_script(nuke, last_frame=100)

    assert nuke.cleared is False


def test_paste_nodes_is_additive_and_preserves_existing_nodes(tmp_path):
    script = tmp_path / "clip.nk"
    script.write_text("Blur {}", encoding="utf-8")
    existing = FakeNode("Read1", "Read")
    added = FakeNode("Blur1", "Blur")
    nuke = FakeNuke(nodes=[existing], paste_result=[added])

    result = paste_nodes(nuke, str(script))

    assert result["added_node_count"] == 1
    assert result["preserved_node_count"] == 1
    assert result["truncated"] is False
    assert result["added_nodes"] == [{"name": "Blur1", "class": "Blur"}]
    assert {node.name() for node in nuke.nodes} == {"Read1", "Blur1"}


def test_paste_nodes_rejects_non_absolute_or_missing_paths():
    nuke = FakeNuke(nodes=[FakeNode("Read1", "Read")])

    with pytest.raises(ScriptLifecycleValidationError):
        paste_nodes(nuke, "clip.nk")

    with pytest.raises(ScriptLifecycleValidationError):
        paste_nodes(nuke, str(Path("C:/definitely/missing/clip.nk")))


def test_paste_nodes_rejects_wrong_suffix(tmp_path):
    not_nk = tmp_path / "clip.txt"
    not_nk.write_text("x", encoding="utf-8")
    nuke = FakeNuke(nodes=[FakeNode("Read1", "Read")])

    with pytest.raises(ScriptLifecycleValidationError):
        paste_nodes(nuke, str(not_nk))


def test_paste_nodes_fails_closed_when_paste_drops_existing_nodes(tmp_path):
    script = tmp_path / "clip.nk"
    script.write_text("Blur {}", encoding="utf-8")
    existing = FakeNode("Read1", "Read")
    added = FakeNode("Blur1", "Blur")
    nuke = FakeNuke(nodes=[existing], paste_result=[added], paste_drops_existing=True)

    with pytest.raises(ScriptLifecycleRuntimeError):
        paste_nodes(nuke, str(script))

    assert added not in nuke.nodes


def test_paste_nodes_recovers_added_nodes_when_paste_raises(tmp_path):
    script = tmp_path / "clip.nk"
    script.write_text("Blur {}", encoding="utf-8")
    existing = FakeNode("Read1", "Read")
    partial = FakeNode("Blur1", "Blur")
    nuke = FakeNuke(nodes=[existing], paste_result=[partial], paste_raises=True)

    with pytest.raises(ScriptLifecycleRuntimeError):
        paste_nodes(nuke, str(script))

    assert [node.name() for node in nuke.nodes] == ["Read1"]
    assert partial in nuke.deleted


def test_paste_nodes_fails_when_nothing_is_added(tmp_path):
    script = tmp_path / "clip.nk"
    script.write_text("", encoding="utf-8")
    nuke = FakeNuke(nodes=[FakeNode("Read1", "Read")], paste_result=[])

    with pytest.raises(ScriptLifecycleRuntimeError):
        paste_nodes(nuke, str(script))


def test_paste_nodes_bounds_reported_added_nodes(tmp_path):
    script = tmp_path / "clip.nk"
    script.write_text("", encoding="utf-8")
    added = [FakeNode(f"Blur{index}", "Blur") for index in range(10)]
    nuke = FakeNuke(nodes=[], paste_result=added)

    result = paste_nodes(nuke, str(script), max_reported_nodes=3)

    assert result["added_node_count"] == 10
    assert result["returned_added_count"] == 3
    assert result["truncated"] is True
    assert len(result["added_nodes"]) == 3


def test_script_lifecycle_verbs_declare_bounded_main_thread_and_paired_writes():
    from dcc_mcp_core import validate_skill
    from dcc_mcp_core.verification.lint import find_unpaired_write_verbs

    report = validate_skill(str(SKILL_ROOT))
    assert report.is_clean, [issue.message for issue in report.issues]

    manifest = yaml.safe_load((SKILL_ROOT / "tools.yaml").read_text(encoding="utf-8"))
    tools = {tool["name"]: tool for tool in manifest["tools"]}
    assert {"get_root_settings", "set_root_settings", "new_script", "paste_nodes"} <= set(tools)
    assert all(
        tools[name]["affinity"] == "main"
        for name in ("get_root_settings", "set_root_settings", "new_script", "paste_nodes")
    )
    assert tools["get_root_settings"]["read_only"] is True
    assert tools["set_root_settings"]["read_only"] is False
    assert tools["new_script"]["destructive"] is True
    assert find_unpaired_write_verbs(manifest["tools"]) == []


def test_server_loads_script_lifecycle_on_the_main_thread_bridge(monkeypatch):
    from dcc_mcp_nuke.dispatcher import NukeDispatcher
    from dcc_mcp_nuke.server import NukeMcpServer

    monkeypatch.setattr(NukeDispatcher, "start", lambda _self: None)
    monkeypatch.setattr(NukeDispatcher, "stop", lambda _self: None)
    server = NukeMcpServer(port=0)
    try:
        server.register_builtin_actions()
        assert server.load_skill("nuke-script") is True
        action = next(item for item in server.list_actions() if item["name"] == "nuke_script__set_root_settings")
        assert action["thread_affinity"] == "main"
        assert action["enforce_thread_affinity"] is True
    finally:
        server.stop()


def test_script_lifecycle_routes_reject_invalid_and_return_success(monkeypatch, tmp_path):
    knobs = _root_knobs()
    nuke = FakeNuke(knobs)
    monkeypatch.setitem(sys.modules, "nuke", nuke)

    read_route = _load_route("get_root_settings")
    ok = read_route.main.__wrapped__()
    assert ok["success"] is True
    assert ok["context"]["format"] == "HD_1080"

    set_route = _load_route("set_root_settings")
    bad = set_route.main.__wrapped__(format="NOPE")
    assert bad["success"] is False
    assert knobs["format"].value() == "HD_1080"


def test_script_lifecycle_contract_is_in_package_surface_and_documented():
    package = ROOT / "src" / "dcc_mcp_nuke"
    skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert package.joinpath("script_lifecycle.py").exists()
    for name in ("get_root_settings", "set_root_settings", "new_script", "paste_nodes"):
        assert (SKILL_ROOT / "scripts" / f"{name}.py").exists()
    assert "frame range" in skill_text
    assert "paste_nodes" in skill_text
    assert "nuke-script" in readme
    assert "paste_nodes" in readme
    assert "nuke-color" in readme
