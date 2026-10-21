from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from dcc_mcp_nuke.color_management import (
    ColorManagementRuntimeError,
    ColorManagementValidationError,
    describe_color_management,
    set_display_transform,
    set_ocio_config,
    set_read_colorspace,
    set_write_colorspace,
)

ROOT = Path(__file__).parents[1]
SKILL_ROOT = ROOT / "src" / "dcc_mcp_nuke" / "skills" / "nuke-color"


class FakeKnob:
    """Enum knob whose ``values`` fails when it is not an enumeration."""

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


class DependentViewKnob:
    """View knob whose enumerated list depends on the current display value."""

    def __init__(self, value, display_knob, views_by_display):
        self.current = value
        self.display_knob = display_knob
        self.views_by_display = views_by_display

    def value(self):
        return self.current

    def setValue(self, value):
        self.current = value

    def values(self):
        return list(self.views_by_display.get(self.display_knob.value(), []))


class FakeNode:
    def __init__(self, name, node_class, knobs=None):
        self._name = name
        self._class = node_class
        self._knobs = dict(knobs or {})

    def name(self):
        return self._name

    def fullName(self):
        return self._name

    def Class(self):
        return self._class

    def knobs(self):
        return self._knobs

    def __getitem__(self, name):
        return self._knobs[name]


class FakeRoot:
    def __init__(self, knobs):
        self._knobs = dict(knobs)

    def __getitem__(self, name):
        if name not in self._knobs:
            raise KeyError(name)
        return self._knobs[name]


class FakeNuke:
    def __init__(self, root_knobs=None, nodes=(), colorspaces=None):
        self._root = FakeRoot(root_knobs or {})
        self.nodes = list(nodes)
        self._colorspaces = colorspaces

    def root(self):
        return self._root

    def toNode(self, name):
        return next((node for node in self.nodes if node.name() == name), None)

    def colorspaces(self, *_args):
        if self._colorspaces is None:
            raise RuntimeError("colorspaces unavailable")
        return list(self._colorspaces)


def _load_route(name):
    path = SKILL_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"nuke_color_route_{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ocio_root(config="aces_1.2", presets=("aces_1.2", "nuke-default", "custom")):
    return {
        "colorManagement": FakeKnob("OCIO"),
        "workingSpaceLUT": FakeKnob("linear"),
        "OCIOConfig": FakeKnob(config, values=list(presets)),
    }


def test_describe_color_management_reports_mode_config_and_enumerations():
    nuke = FakeNuke(_ocio_root(), colorspaces=["linear", "sRGB", "rec709"])

    result = describe_color_management(nuke)

    assert result["color_management"] == "OCIO"
    assert result["ocio_config"] == "aces_1.2"
    assert result["available_ocio_configs"] == ["aces_1.2", "nuke-default", "custom"]
    assert result["working_space"] == "linear"
    assert result["colorspaces"] == ["linear", "sRGB", "rec709"]
    assert result["colorspaces_available"] is True
    assert result["colorspace_count"] == 3
    assert result["colorspaces_truncated"] is False


def test_describe_color_management_bounds_colorspace_enumeration():
    many = [f"cs{index:03d}" for index in range(40)]
    nuke = FakeNuke(_ocio_root(), colorspaces=many)

    result = describe_color_management(nuke, max_colorspaces=5)

    assert result["colorspaces"] == many[:5]
    assert result["colorspace_count"] == 40
    assert result["colorspaces_truncated"] is True


def test_describe_color_management_reflects_unavailable_ocio_as_empty_enumeration():
    nuke = FakeNuke({"colorManagement": FakeKnob("Nuke")}, colorspaces=[])

    result = describe_color_management(nuke)

    assert result["color_management"] == "Nuke"
    assert result["ocio_config"] is None
    assert result["available_ocio_configs"] == []
    assert result["colorspaces"] == []
    assert result["colorspaces_available"] is False


def test_set_ocio_config_sets_from_enumerated_presets_and_reads_back():
    ocio = FakeKnob("nuke-default", values=["aces_1.2", "nuke-default"])
    nuke = FakeNuke({"OCIOConfig": ocio, "colorManagement": FakeKnob("OCIO")})

    result = set_ocio_config(nuke, "aces_1.2")

    assert result["ocio_config"] == "aces_1.2"
    assert result["color_management"] == "OCIO"
    assert ocio.value() == "aces_1.2"


def test_set_ocio_config_rejects_unknown_name_without_mutating():
    ocio = FakeKnob("nuke-default", values=["aces_1.2", "nuke-default"])
    nuke = FakeNuke({"OCIOConfig": ocio})

    with pytest.raises(ColorManagementValidationError):
        set_ocio_config(nuke, "does-not-exist")

    assert ocio.value() == "nuke-default"


def test_set_ocio_config_fails_closed_when_presets_are_not_enumerable():
    ocio = FakeKnob("nuke-default")
    nuke = FakeNuke({"OCIOConfig": ocio})

    with pytest.raises(ColorManagementValidationError):
        set_ocio_config(nuke, "aces_1.2")


def test_set_ocio_config_rolls_back_on_readback_mismatch():
    ocio = FakeKnob("nuke-default", values=["aces_1.2", "nuke-default"], reject={"aces_1.2"})
    nuke = FakeNuke({"OCIOConfig": ocio})

    with pytest.raises(ColorManagementRuntimeError):
        set_ocio_config(nuke, "aces_1.2")

    assert ocio.value() == "nuke-default"


def test_set_read_colorspace_sets_from_the_node_enumeration():
    colorspace = FakeKnob("sRGB", values=["linear", "sRGB", "ACEScg"])
    read = FakeNode("Read1", "Read", {"colorspace": colorspace})
    nuke = FakeNuke(nodes=[read])

    result = set_read_colorspace(nuke, "Read1", "ACEScg")

    assert result == {"node": "Read1", "class": "Read", "colorspace": "ACEScg", "available_colorspaces": 3}
    assert colorspace.value() == "ACEScg"


def test_set_read_colorspace_rejects_unknown_colorspace():
    colorspace = FakeKnob("sRGB", values=["linear", "sRGB"])
    read = FakeNode("Read1", "Read", {"colorspace": colorspace})
    nuke = FakeNuke(nodes=[read])

    with pytest.raises(ColorManagementValidationError):
        set_read_colorspace(nuke, "Read1", "not-a-colorspace")

    assert colorspace.value() == "sRGB"


def test_set_read_colorspace_rejects_wrong_node_class():
    colorspace = FakeKnob("sRGB", values=["sRGB"])
    write = FakeNode("Write1", "Write", {"colorspace": colorspace})
    nuke = FakeNuke(nodes=[write])

    with pytest.raises(ColorManagementValidationError):
        set_read_colorspace(nuke, "Write1", "sRGB")


def test_set_write_colorspace_rolls_back_on_readback_mismatch():
    colorspace = FakeKnob("sRGB", values=["linear", "sRGB"], reject={"linear"})
    write = FakeNode("Write1", "Write", {"colorspace": colorspace})
    nuke = FakeNuke(nodes=[write])

    with pytest.raises(ColorManagementRuntimeError):
        set_write_colorspace(nuke, "Write1", "linear")

    assert colorspace.value() == "sRGB"


def test_set_display_transform_sets_display_then_view():
    display = FakeKnob("sRGB", values=["sRGB", "rec709"])
    view = FakeKnob("Raw", values=["Raw", "Film"])
    node = FakeNode("OCIODisplay1", "OCIODisplay", {"display": display, "view": view})
    nuke = FakeNuke(nodes=[node])

    result = set_display_transform(nuke, "OCIODisplay1", "rec709", "Film")

    assert result == {"node": "OCIODisplay1", "class": "OCIODisplay", "display": "rec709", "view": "Film"}
    assert display.value() == "rec709"
    assert view.value() == "Film"


def test_set_display_transform_resolves_view_from_the_selected_display():
    display = FakeKnob("sRGB", values=["sRGB", "rec709"])
    view = DependentViewKnob("Raw", display, {"sRGB": ["Raw", "sRGB"], "rec709": ["Raw", "rec709"]})
    node = FakeNode("OCIODisplay1", "OCIODisplay", {"display": display, "view": view})
    nuke = FakeNuke(nodes=[node])

    set_display_transform(nuke, "OCIODisplay1", "rec709", "rec709")
    assert (display.value(), view.value()) == ("rec709", "rec709")

    with pytest.raises(ColorManagementValidationError):
        set_display_transform(nuke, "OCIODisplay1", "sRGB", "rec709")

    assert display.value() == "rec709"
    assert view.value() == "rec709"


def test_set_display_transform_rejects_unknown_display():
    display = FakeKnob("sRGB", values=["sRGB", "rec709"])
    view = FakeKnob("Raw", values=["Raw"])
    node = FakeNode("OCIODisplay1", "OCIODisplay", {"display": display, "view": view})
    nuke = FakeNuke(nodes=[node])

    with pytest.raises(ColorManagementValidationError):
        set_display_transform(nuke, "OCIODisplay1", "ACES", "Raw")


def test_set_display_transform_rejects_wrong_node_class():
    node = FakeNode(
        "Grade1", "Grade", {"display": FakeKnob("sRGB", values=["sRGB"]), "view": FakeKnob("Raw", values=["Raw"])}
    )
    nuke = FakeNuke(nodes=[node])

    with pytest.raises(ColorManagementValidationError):
        set_display_transform(nuke, "Grade1", "sRGB", "Raw")


def test_color_management_skill_declares_bounded_main_thread_verbs():
    from dcc_mcp_core import validate_skill

    report = validate_skill(str(SKILL_ROOT))
    assert report.is_clean, [issue.message for issue in report.issues]

    manifest = yaml.safe_load((SKILL_ROOT / "tools.yaml").read_text(encoding="utf-8"))
    tools = {tool["name"]: tool for tool in manifest["tools"]}
    assert set(tools) == {
        "get_color_management",
        "set_ocio_config",
        "set_read_colorspace",
        "set_write_colorspace",
        "set_display_transform",
    }
    assert all(tool["affinity"] == "main" for tool in tools.values())
    assert tools["get_color_management"]["read_only"] is True
    assert all(
        tools[name]["read_only"] is False
        for name in ("set_ocio_config", "set_read_colorspace", "set_write_colorspace", "set_display_transform")
    )


def test_color_management_write_verbs_are_paired_with_a_readback():
    from dcc_mcp_core.verification.lint import find_unpaired_write_verbs

    manifest = yaml.safe_load((SKILL_ROOT / "tools.yaml").read_text(encoding="utf-8"))

    assert find_unpaired_write_verbs(manifest["tools"]) == []


def test_server_loads_color_management_on_the_main_thread_bridge(monkeypatch):
    from dcc_mcp_nuke.dispatcher import NukeDispatcher
    from dcc_mcp_nuke.server import NukeMcpServer

    monkeypatch.setattr(NukeDispatcher, "start", lambda _self: None)
    monkeypatch.setattr(NukeDispatcher, "stop", lambda _self: None)
    server = NukeMcpServer(port=0)
    try:
        server.register_builtin_actions()
        assert server.load_skill("nuke-color") is True
        action = next(item for item in server.list_actions() if item["name"] == "nuke_color__set_read_colorspace")
        assert action["thread_affinity"] == "main"
        assert action["enforce_thread_affinity"] is True
    finally:
        server.stop()


def test_color_routes_return_success_and_reject_invalid_names(monkeypatch):
    ocio = FakeKnob("aces_1.2", values=["aces_1.2", "nuke-default"])
    nuke = FakeNuke(
        {"colorManagement": FakeKnob("OCIO"), "workingSpaceLUT": FakeKnob("linear"), "OCIOConfig": ocio},
        colorspaces=["linear", "sRGB"],
    )
    monkeypatch.setitem(sys.modules, "nuke", nuke)

    describe_route = _load_route("get_color_management")
    ok = describe_route.main.__wrapped__()
    assert ok["success"] is True
    assert ok["context"]["ocio_config"] == "aces_1.2"

    colorspace = FakeKnob("sRGB", values=["linear", "sRGB"])
    read = FakeNode("Read1", "Read", {"colorspace": colorspace})
    nuke.nodes = [read]
    write_route = _load_route("set_read_colorspace")
    bad = write_route.main.__wrapped__(node_name="Read1", colorspace="not-a-colorspace")
    assert bad["success"] is False
    assert colorspace.value() == "sRGB"


def test_color_route_redacts_unexpected_host_failure(monkeypatch):
    class FailingNuke(FakeNuke):
        def root(self):
            raise OSError("private/project/secret")

    monkeypatch.setitem(sys.modules, "nuke", FailingNuke())
    route = _load_route("get_color_management")

    result = route.main.__wrapped__()

    assert result["success"] is False
    assert "private" not in str(result)


def test_color_management_contract_is_in_package_surface_and_documented():
    package = ROOT / "src" / "dcc_mcp_nuke"
    skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert package.joinpath("color_management.py").exists()
    assert (SKILL_ROOT / "scripts" / "get_color_management.py").exists()
    assert "OCIO" in skill_text
    assert "enumerated" in skill_text
    assert "nuke-color" in readme
    assert "colorspace" in readme
