from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest
import yaml
from jsonschema import ValidationError, validate

from dcc_mcp_nuke.text_layout import upsert_text2_label

ROOT = Path(__file__).parents[1]
SKILL_ROOT = ROOT / "src" / "dcc_mcp_nuke" / "skills" / "nuke-text-layout"
TOOL_ROUTE = SKILL_ROOT / "scripts" / "upsert_text2_label.py"


class FakeKnob:
    def __init__(self, value, *, ignore_value=None):
        self.current = value
        self.ignore_value = ignore_value

    def value(self):
        return self.current

    def setValue(self, value):
        if value == self.ignore_value:
            return
        self.current = value


class FakeNode:
    def __init__(self, name="Text2", node_class="Text2"):
        self._name = name
        self._class = node_class
        self._x = 0
        self._y = 0
        self._knobs = {
            "message": FakeKnob(""),
            "global_font_scale": FakeKnob(1.0),
            "box": FakeKnob([0.0, 0.0, 1920.0, 1080.0]),
            "xjustify": FakeKnob("left"),
            "yjustify": FakeKnob("baseline"),
        }

    def name(self):
        return self._name

    def setName(self, name, **_kwargs):
        self._name = name

    def Class(self):
        return self._class

    def knobs(self):
        return self._knobs

    def __getitem__(self, name):
        return self._knobs[name]

    def setXYpos(self, x, y):
        self._x, self._y = x, y

    def xpos(self):
        return self._x

    def ypos(self):
        return self._y


class FakeNuke:
    def __init__(self, nodes=()):
        self.nodes = list(nodes)

    def toNode(self, name):
        return next((node for node in self.nodes if node.name() == name), None)

    def createNode(self, node_class, inpanel=False):
        assert node_class == "Text2"
        assert inpanel is False
        node = FakeNode()
        self.nodes.append(node)
        return node

    def delete(self, node):
        self.nodes.remove(node)


def _load_tool_route():
    spec = importlib.util.spec_from_file_location("nuke_text_layout_route", TOOL_ROUTE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upsert_text2_label_production_route_creates_and_reads_back_layout(monkeypatch):
    fake_nuke = FakeNuke()
    monkeypatch.setitem(sys.modules, "nuke", fake_nuke)
    route = _load_tool_route()

    result = route.main(
        node_name="ShotLabel",
        text="SHOT 010",
        font_size_px=32.0,
        x=120,
        y=240,
        box_x=10.0,
        box_y=20.0,
        box_width=640.0,
        box_height=80.0,
        horizontal_justify="center",
        vertical_justify="center",
    )

    assert result["success"] is True
    context = result["context"]
    assert context["created"] is True
    assert context["node"] == {
        "name": "ShotLabel",
        "class": "Text2",
        "x": 120,
        "y": 240,
    }
    assert context["layout"] == {
        "text": "SHOT 010",
        "font_size_px": 32.0,
        "global_font_scale": 0.5,
        "box": {"x": 10.0, "y": 20.0, "width": 640.0, "height": 80.0},
        "horizontal_justify": "center",
        "vertical_justify": "center",
    }


def _request(**overrides):
    values = {
        "node_name": "ShotLabel",
        "text": "SHOT 010",
        "font_size_px": 32.0,
        "x": 120,
        "y": 240,
        "box_x": 10.0,
        "box_y": 20.0,
        "box_width": 640.0,
        "box_height": 80.0,
        "horizontal_justify": "center",
        "vertical_justify": "center",
    }
    values.update(overrides)
    return values


def test_upsert_updates_existing_text2_and_is_idempotent():
    node = FakeNode("ShotLabel")
    nuke = FakeNuke([node])

    first = upsert_text2_label(nuke, **_request())
    second = upsert_text2_label(nuke, **_request())

    assert first["created"] is False
    assert second == first
    assert nuke.nodes == [node]
    assert node["global_font_scale"].value() == 0.5


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"node_name": "bad name"}, "node_name must be a bounded Nuke identifier"),
        ({"text": ""}, "text must contain between 1 and 4096 characters"),
        ({"text": "x" * 4097}, "text must contain between 1 and 4096 characters"),
        ({"font_size_px": math.nan}, "font_size_px must be a finite number between 1.0 and 4096.0"),
        ({"font_size_px": 0.99}, "font_size_px must be a finite number between 1.0 and 4096.0"),
        ({"font_size_px": 4096.01}, "font_size_px must be a finite number between 1.0 and 4096.0"),
        ({"x": True}, "x must be an integer between -1000000 and 1000000"),
        ({"box_width": 0.0}, "box_width must be a finite number between 1.0 and 1000000.0"),
        ({"horizontal_justify": "script"}, "horizontal_justify is not supported"),
        ({"vertical_justify": "python"}, "vertical_justify is not supported"),
    ],
)
def test_invalid_parameters_fail_before_mutation(overrides, message):
    nuke = FakeNuke()

    with pytest.raises(ValueError, match=message):
        upsert_text2_label(nuke, **_request(**overrides))

    assert nuke.nodes == []


def test_existing_non_text2_name_collision_is_rejected_without_mutation():
    collision = FakeNode("ShotLabel", "NoOp")
    nuke = FakeNuke([collision])

    with pytest.raises(ValueError, match="existing node must be a Text2 node"):
        upsert_text2_label(nuke, **_request())

    assert nuke.nodes == [collision]
    assert collision.knobs()["message"].value() == ""


def test_existing_text2_missing_required_knob_fails_without_rollback_mutation():
    node = FakeNode("ShotLabel")
    del node._knobs["box"]
    nuke = FakeNuke([node])

    with pytest.raises(ValueError, match="Text2 node does not expose the required layout knobs"):
        upsert_text2_label(nuke, **_request())

    assert nuke.nodes == [node]
    assert node["message"].value() == ""


def test_update_readback_mismatch_rolls_back_all_layout_state():
    node = FakeNode("ShotLabel")
    node["message"].setValue("ORIGINAL")
    node._knobs["global_font_scale"] = FakeKnob(1.0, ignore_value=0.5)
    node.setXYpos(7, 9)
    before = {name: knob.value() for name, knob in node.knobs().items()}
    nuke = FakeNuke([node])

    with pytest.raises(RuntimeError, match="Text2 label readback does not match request"):
        upsert_text2_label(nuke, **_request())

    assert {name: knob.value() for name, knob in node.knobs().items()} == before
    assert (node.xpos(), node.ypos()) == (7, 9)


def test_create_readback_mismatch_deletes_partial_node():
    class MismatchNuke(FakeNuke):
        def createNode(self, node_class, inpanel=False):
            node = super().createNode(node_class, inpanel=inpanel)
            node._knobs["global_font_scale"] = FakeKnob(1.0, ignore_value=0.5)
            return node

    nuke = MismatchNuke()

    with pytest.raises(RuntimeError, match="Text2 label readback does not match request"):
        upsert_text2_label(nuke, **_request())

    assert nuke.nodes == []


def test_text_layout_skill_schema_runtime_and_deadline_contract():
    from dcc_mcp_core import validate_skill

    report = validate_skill(str(SKILL_ROOT))
    assert report.is_clean, [issue.message for issue in report.issues]

    manifest = yaml.safe_load((SKILL_ROOT / "tools.yaml").read_text(encoding="utf-8"))
    assert len(manifest["tools"]) == 1
    tool = manifest["tools"][0]
    assert tool["name"] == "upsert_text2_label"
    assert tool["execution"] == "sync"
    assert tool["affinity"] == "main"
    assert tool["timeout_hint_secs"] == 30
    assert tool["idempotent"] is True
    schema = tool["input_schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "node_name",
        "text",
        "font_size_px",
        "x",
        "y",
        "box_x",
        "box_y",
        "box_width",
        "box_height",
    }
    assert {"python", "tcl", "script", "node_class", "font_size"}.isdisjoint(schema["properties"])
    assert schema["properties"]["font_size_px"] == {
        "type": "number",
        "minimum": 1.0,
        "maximum": 4096.0,
    }
    missing_text = _request()
    del missing_text["text"]
    with pytest.raises(ValidationError):
        validate(missing_text, schema)


def test_server_loads_text_layout_on_the_existing_main_thread_bridge(monkeypatch):
    from dcc_mcp_nuke.dispatcher import NukeDispatcher
    from dcc_mcp_nuke.server import NukeMcpServer

    monkeypatch.setattr(NukeDispatcher, "start", lambda _self: None)
    monkeypatch.setattr(NukeDispatcher, "stop", lambda _self: None)
    server = NukeMcpServer(port=0)
    try:
        server.register_builtin_actions()
        assert server.load_skill("nuke-text-layout") is True
        action = next(item for item in server.list_actions() if item["name"] == "nuke_text_layout__upsert_text2_label")
        assert action["thread_affinity"] == "main"
        assert action["enforce_thread_affinity"] is True
        assert server._execution_bridge is server._nuke_execution_bridge
    finally:
        server.stop()


def test_tool_route_redacts_unexpected_host_failure(monkeypatch):
    class FailingNuke(FakeNuke):
        def toNode(self, name):
            raise OSError(f"private/project/path/{name}")

    monkeypatch.setitem(sys.modules, "nuke", FailingNuke())
    route = _load_tool_route()

    result = route.main.__wrapped__(**_request())

    assert result["success"] is False
    assert result["error"] == "failed to apply Text2 label layout"
    assert "private" not in str(result)


def test_text_layout_contract_is_in_package_surface_and_documented():
    package = ROOT / "src" / "dcc_mcp_nuke"
    skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert package.joinpath("text_layout.py").exists()
    assert TOOL_ROUTE.exists()
    assert "global_font_scale" in skill_text
    assert "Text2" in skill_text
    assert "nuke-text-layout" in readme
