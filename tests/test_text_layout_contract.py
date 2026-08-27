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

    def isAnimated(self, **_kwargs):
        return False

    def hasExpression(self, **_kwargs):
        return False

    def animations(self, **_kwargs):
        return []

    def getNumKeys(self, **_kwargs):
        return 0


class DynamicFakeKnob(FakeKnob):
    def __init__(
        self,
        value,
        *,
        animated=False,
        expression=False,
        animations=(),
        key_count=0,
    ):
        super().__init__(value)
        self.animated = animated
        self.expression = expression
        self.animation_curves = list(animations)
        self.key_count = key_count
        self.set_calls = []

    def setValue(self, value):
        self.set_calls.append(value)
        if self.animated:
            self.key_count += 1
        super().setValue(value)

    def isAnimated(self, **_kwargs):
        return self.animated

    def hasExpression(self, **_kwargs):
        return self.expression

    def animations(self, **_kwargs):
        return list(self.animation_curves)

    def getNumKeys(self, **_kwargs):
        return self.key_count


class FakeAnimationCurve:
    def __init__(self, view, keys):
        self.view = view
        self._keys = list(keys)

    def keys(self):
        return list(self._keys)


class HostileEmptyAnimations:
    def __len__(self):
        return 0

    def __iter__(self):
        raise RuntimeError("hostile iterator")


class RaisingAnimationsIterator:
    def __iter__(self):
        raise RuntimeError("iterator failed")


class FalsyProbeResult:
    def __bool__(self):
        return False


class ProbeShapeKnob(DynamicFakeKnob):
    def __init__(self, value, *, probe, result):
        super().__init__(value)
        self.probe = probe
        self.result = result

    def isAnimated(self, **_kwargs):
        return self.result if self.probe == "isAnimated" else False

    def hasExpression(self, **_kwargs):
        return self.result if self.probe == "hasExpression" else False

    def animations(self, **_kwargs):
        return self.result if self.probe == "animations" else []

    def getNumKeys(self, **_kwargs):
        return self.result if self.probe == "getNumKeys" else self.key_count


class HostileFloat(float):
    def __float__(self):
        raise RuntimeError("hostile float conversion")


class HostileBox(list):
    def __len__(self):
        raise RuntimeError("hostile box length")

    def __iter__(self):
        raise RuntimeError("hostile box iteration")


class ReadbackOverrideKnob(FakeKnob):
    def __init__(self, value, *, requested, readback):
        super().__init__(value)
        self.requested = requested
        self.readback = readback

    def setValue(self, value):
        self.current = self.readback if value == self.requested else value


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

    def allNodes(self, recurseGroups=True):
        assert recurseGroups is True
        return list(self.nodes)

    def allNodeClasses(self):
        return ["Text2"]

    def views(self):
        return ["main"]

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


def test_tool_route_rejects_bool_font_scale_readback_and_rolls_back(monkeypatch):
    class BoolScaleReadbackKnob(FakeKnob):
        def setValue(self, value):
            self.current = True if value == 1.0 else value

    node = FakeNode("ShotLabel")
    node._knobs["global_font_scale"] = BoolScaleReadbackKnob(0.5)
    node.setXYpos(7, 9)
    before = {name: knob.value() for name, knob in node.knobs().items()}
    nuke = FakeNuke([node])
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    route = _load_tool_route()

    result = route.main.__wrapped__(**_request(font_size_px=64.0))

    assert result["success"] is False
    assert result["error"] == "Text2 label readback does not match request"
    assert {name: knob.value() for name, knob in node.knobs().items()} == before
    assert (node.xpos(), node.ypos()) == (7, 9)


def test_bool_box_element_readback_fails_closed_and_rolls_back():
    class BoolBoxReadbackKnob(FakeKnob):
        def setValue(self, value):
            self.current = [True, *value[1:]] if value == [1.0, 20.0, 641.0, 100.0] else value

    node = FakeNode("ShotLabel")
    node._knobs["box"] = BoolBoxReadbackKnob([0.0, 0.0, 1920.0, 1080.0])
    node.setXYpos(7, 9)
    before = {name: knob.value() for name, knob in node.knobs().items()}
    nuke = FakeNuke([node])

    with pytest.raises(RuntimeError, match="Text2 label readback does not match request"):
        upsert_text2_label(nuke, **_request(box_x=1.0))

    assert {name: knob.value() for name, knob in node.knobs().items()} == before
    assert (node.xpos(), node.ypos()) == (7, 9)


def test_bool_position_readback_fails_closed_and_rolls_back():
    class BoolPositionNode(FakeNode):
        def setXYpos(self, x, y):
            self._x, self._y = (True, y) if (x, y) == (1, 240) else (x, y)

    node = BoolPositionNode("ShotLabel")
    node.setXYpos(7, 9)
    before = {name: knob.value() for name, knob in node.knobs().items()}
    nuke = FakeNuke([node])

    with pytest.raises(RuntimeError, match="Text2 label readback does not match request"):
        upsert_text2_label(nuke, **_request(x=1))

    assert {name: knob.value() for name, knob in node.knobs().items()} == before
    assert (node.xpos(), node.ypos()) == (7, 9)


_INVALID_NUMERIC_READBACKS = [
    pytest.param(lambda: True, id="bool"),
    pytest.param(lambda: math.nan, id="nan"),
    pytest.param(lambda: math.inf, id="positive-infinity"),
    pytest.param(lambda: -math.inf, id="negative-infinity"),
    pytest.param(lambda: "1.0", id="string"),
    pytest.param(lambda: [1.0], id="container"),
    pytest.param(object, id="object"),
    pytest.param(lambda: HostileFloat(1.0), id="hostile-float-subclass"),
]


@pytest.mark.parametrize("readback_factory", _INVALID_NUMERIC_READBACKS)
def test_invalid_font_scale_readback_fails_closed_and_rolls_back(readback_factory):
    node = FakeNode("ShotLabel")
    node._knobs["global_font_scale"] = ReadbackOverrideKnob(1.0, requested=0.5, readback=readback_factory())
    node.setXYpos(7, 9)
    before = {name: knob.value() for name, knob in node.knobs().items()}
    nuke = FakeNuke([node])

    with pytest.raises(RuntimeError, match="Text2 label readback does not match request"):
        upsert_text2_label(nuke, **_request())

    assert {name: knob.value() for name, knob in node.knobs().items()} == before
    assert (node.xpos(), node.ypos()) == (7, 9)


@pytest.mark.parametrize("index", range(4))
@pytest.mark.parametrize("readback_factory", _INVALID_NUMERIC_READBACKS)
def test_invalid_box_element_readback_fails_closed_and_rolls_back(index, readback_factory):
    requested = [10.0, 20.0, 650.0, 100.0]
    readback = list(requested)
    readback[index] = readback_factory()
    node = FakeNode("ShotLabel")
    node._knobs["box"] = ReadbackOverrideKnob([0.0, 0.0, 1920.0, 1080.0], requested=requested, readback=readback)
    node.setXYpos(7, 9)
    before = {name: knob.value() for name, knob in node.knobs().items()}
    nuke = FakeNuke([node])

    with pytest.raises(RuntimeError, match="Text2 label readback does not match request"):
        upsert_text2_label(nuke, **_request())

    assert {name: knob.value() for name, knob in node.knobs().items()} == before
    assert (node.xpos(), node.ypos()) == (7, 9)


_INVALID_BOX_SHAPES = [
    pytest.param(lambda: None, id="none"),
    pytest.param(object, id="object"),
    pytest.param(lambda: "10,20,650,100", id="string"),
    pytest.param(lambda: [10.0, 20.0, 650.0], id="short-list"),
    pytest.param(lambda: (10.0, 20.0, 650.0), id="short-tuple"),
    pytest.param(lambda: [10.0, 20.0, 650.0, 100.0, 0.0], id="long-list"),
    pytest.param(lambda: (value for value in (10.0, 20.0, 650.0, 100.0)), id="generator"),
    pytest.param(lambda: HostileBox([10.0, 20.0, 650.0, 100.0]), id="hostile-list-subclass"),
]


@pytest.mark.parametrize("readback_factory", _INVALID_BOX_SHAPES)
def test_invalid_box_readback_shape_fails_closed_and_rolls_back(readback_factory):
    requested = [10.0, 20.0, 650.0, 100.0]
    node = FakeNode("ShotLabel")
    node._knobs["box"] = ReadbackOverrideKnob(
        [0.0, 0.0, 1920.0, 1080.0], requested=requested, readback=readback_factory()
    )
    node.setXYpos(7, 9)
    before = {name: knob.value() for name, knob in node.knobs().items()}
    nuke = FakeNuke([node])

    with pytest.raises(RuntimeError, match="Text2 label readback does not match request"):
        upsert_text2_label(nuke, **_request())

    assert {name: knob.value() for name, knob in node.knobs().items()} == before
    assert (node.xpos(), node.ypos()) == (7, 9)


@pytest.mark.parametrize("axis", ["x", "y"])
@pytest.mark.parametrize("readback_factory", _INVALID_NUMERIC_READBACKS)
def test_invalid_position_readback_fails_closed_and_rolls_back(axis, readback_factory):
    class InvalidPositionNode(FakeNode):
        def setXYpos(self, x, y):
            if (x, y) == (120, 240):
                self._x = readback_factory() if axis == "x" else x
                self._y = readback_factory() if axis == "y" else y
            else:
                self._x, self._y = x, y

    node = InvalidPositionNode("ShotLabel")
    node.setXYpos(7, 9)
    before = {name: knob.value() for name, knob in node.knobs().items()}
    nuke = FakeNuke([node])

    with pytest.raises(RuntimeError, match="Text2 label readback does not match request"):
        upsert_text2_label(nuke, **_request())

    assert {name: knob.value() for name, knob in node.knobs().items()} == before
    assert (node.xpos(), node.ypos()) == (7, 9)


def test_invalid_numeric_readback_on_new_text2_deletes_partial_node():
    class InvalidReadbackNuke(FakeNuke):
        def createNode(self, node_class, inpanel=False):
            node = super().createNode(node_class, inpanel=inpanel)
            node._knobs["global_font_scale"] = ReadbackOverrideKnob(1.0, requested=0.5, readback=True)
            return node

    nuke = InvalidReadbackNuke()

    with pytest.raises(RuntimeError, match="Text2 label readback does not match request"):
        upsert_text2_label(nuke, **_request())

    assert nuke.nodes == []


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


@pytest.mark.parametrize(
    "text",
    [
        "[value root.name]",
        '[python {"REVIEW_MARKER"}]',
        "escaped\\[value root.name]",
        "left [ bracket",
        "right ] bracket",
        "line\nfeed",
        "null\x00byte",
        "escape\x1bsequence",
    ],
)
def test_executable_or_control_shaped_text_is_rejected_before_mutation(text):
    nuke = FakeNuke()

    with pytest.raises(ValueError, match="text must be bounded non-executable plain text"):
        upsert_text2_label(nuke, **_request(text=text))

    assert nuke.nodes == []


def test_ordinary_unicode_text_is_not_overblocked():
    nuke = FakeNuke()

    result = upsert_text2_label(nuke, **_request(text="镜头 010 — Café 👩‍💻 (final)!"))

    assert result["layout"]["text"] == "镜头 010 — Café 👩‍💻 (final)!"


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


@pytest.mark.parametrize("knob_name", ["message", "global_font_scale", "box", "xjustify", "yjustify"])
def test_animated_required_knob_fails_before_any_mutation(knob_name):
    node = FakeNode("ShotLabel")
    original = node[knob_name].value()
    dynamic = DynamicFakeKnob(original, animated=True, key_count=2)
    node._knobs[knob_name] = dynamic
    node.setXYpos(7, 9)
    before = {name: knob.value() for name, knob in node.knobs().items()}
    nuke = FakeNuke([node])

    with pytest.raises(RuntimeError, match="Text2 required knobs must be static"):
        upsert_text2_label(nuke, **_request())

    assert {name: knob.value() for name, knob in node.knobs().items()} == before
    assert (node.xpos(), node.ypos()) == (7, 9)
    assert dynamic.set_calls == []
    assert dynamic.key_count == 2
    assert nuke.nodes == [node]


def test_expression_only_required_knob_fails_before_mutation():
    node = FakeNode("ShotLabel")
    expression = DynamicFakeKnob(1.0, expression=True)
    node._knobs["global_font_scale"] = expression
    nuke = FakeNuke([node])

    with pytest.raises(RuntimeError, match="Text2 required knobs must be static"):
        upsert_text2_label(nuke, **_request())

    assert expression.set_calls == []
    assert expression.value() == 1.0


def test_multi_view_animation_curve_fails_before_mutation():
    node = FakeNode("ShotLabel")
    box = DynamicFakeKnob(
        [0.0, 0.0, 1920.0, 1080.0],
        animations=[
            FakeAnimationCurve("left", [1]),
            FakeAnimationCurve("right", [2]),
        ],
    )
    node._knobs["box"] = box
    nuke = FakeNuke([node])

    with pytest.raises(RuntimeError, match="Text2 required knobs must be static"):
        upsert_text2_label(nuke, **_request())

    assert box.set_calls == []
    assert box.animations()[0].view == "left"
    assert box.animations()[1].view == "right"


_HOSTILE_PROBE_RESULTS = [
    pytest.param("isAnimated", lambda: None, id="animated-none"),
    pytest.param("isAnimated", lambda: 0, id="animated-integer-zero"),
    pytest.param("isAnimated", FalsyProbeResult, id="animated-hostile-falsy"),
    pytest.param("hasExpression", lambda: None, id="expression-none"),
    pytest.param("hasExpression", lambda: 0, id="expression-integer-zero"),
    pytest.param("hasExpression", FalsyProbeResult, id="expression-hostile-falsy"),
    pytest.param("animations", lambda: None, id="animations-none"),
    pytest.param("animations", HostileEmptyAnimations, id="animations-hostile-empty"),
    pytest.param("animations", lambda: (item for item in ()), id="animations-generator"),
    pytest.param("animations", RaisingAnimationsIterator, id="animations-iterator-error"),
    pytest.param("animations", lambda: [None], id="animations-none-entry"),
    pytest.param("animations", lambda: [object()], id="animations-invalid-entry"),
    pytest.param("getNumKeys", lambda: False, id="key-count-bool"),
    pytest.param("getNumKeys", lambda: -1, id="key-count-negative"),
]


@pytest.mark.parametrize("knob_name", ["message", "global_font_scale", "box", "xjustify", "yjustify"])
@pytest.mark.parametrize(("probe", "result_factory"), _HOSTILE_PROBE_RESULTS)
def test_unsupported_dynamic_probe_shape_fails_closed_before_mutation(knob_name, probe, result_factory):
    node = FakeNode("ShotLabel")
    original = node[knob_name].value()
    hostile = ProbeShapeKnob(original, probe=probe, result=result_factory())
    node._knobs[knob_name] = hostile
    node.setXYpos(7, 9)
    before = {name: knob.value() for name, knob in node.knobs().items()}
    nuke = FakeNuke([node])

    with pytest.raises(RuntimeError, match="Text2 required knob state could not be verified"):
        upsert_text2_label(nuke, **_request())

    assert {name: knob.value() for name, knob in node.knobs().items()} == before
    assert (node.xpos(), node.ypos()) == (7, 9)
    assert hostile.set_calls == []
    assert hostile.key_count == 0
    assert nuke.nodes == [node]


def test_expression_reported_from_a_non_main_view_fails_before_mutation():
    class ViewExpressionKnob(DynamicFakeKnob):
        expressions_by_view = {"main": False, "left": False, "right": True}

        def hasExpression(self, **_kwargs):
            return any(self.expressions_by_view.values())

    node = FakeNode("ShotLabel")
    expression = ViewExpressionKnob("left")
    node._knobs["xjustify"] = expression
    nuke = FakeNuke([node])

    with pytest.raises(RuntimeError, match="Text2 required knobs must be static"):
        upsert_text2_label(nuke, **_request())

    assert expression.set_calls == []
    assert expression.value() == "left"


def test_keyed_knob_does_not_insert_a_key_or_reach_later_readback_failure():
    node = FakeNode("ShotLabel")
    keyed_scale = DynamicFakeKnob(1.0, key_count=2)
    node._knobs["global_font_scale"] = keyed_scale
    node._knobs["box"] = FakeKnob([0.0, 0.0, 1920.0, 1080.0], ignore_value=[10.0, 20.0, 650.0, 100.0])
    nuke = FakeNuke([node])

    with pytest.raises(RuntimeError, match="Text2 required knobs must be static"):
        upsert_text2_label(nuke, **_request())

    assert keyed_scale.set_calls == []
    assert keyed_scale.key_count == 2
    assert keyed_scale.value() == 1.0
    assert node["box"].value() == [0.0, 0.0, 1920.0, 1080.0]


def test_unobservable_animation_state_returns_a_redacted_error_without_mutation(monkeypatch):
    class UnobservableKnob(FakeKnob):
        def isAnimated(self, **_kwargs):
            raise OSError("private/project/path")

    node = FakeNode("ShotLabel")
    knob = UnobservableKnob(1.0)
    node._knobs["global_font_scale"] = knob
    nuke = FakeNuke([node])
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    route = _load_tool_route()

    result = route.main.__wrapped__(**_request())

    assert result["success"] is False
    assert result["error"] == "Text2 required knob state could not be verified"
    assert "private" not in str(result)
    assert knob.value() == 1.0


def test_dynamic_host_default_on_new_text2_deletes_partial_node():
    class DynamicDefaultNuke(FakeNuke):
        def createNode(self, node_class, inpanel=False):
            node = super().createNode(node_class, inpanel=inpanel)
            node._knobs["global_font_scale"] = DynamicFakeKnob(1.0, animated=True, key_count=2)
            return node

    nuke = DynamicDefaultNuke()

    with pytest.raises(RuntimeError, match="Text2 required knobs must be static"):
        upsert_text2_label(nuke, **_request())

    assert nuke.nodes == []


def test_unverifiable_host_default_on_new_text2_deletes_partial_node_without_mutation():
    class UnverifiableDefaultNuke(FakeNuke):
        def createNode(self, node_class, inpanel=False):
            node = super().createNode(node_class, inpanel=inpanel)
            node._knobs["message"] = ProbeShapeKnob("", probe="animations", result=None)
            return node

    nuke = UnverifiableDefaultNuke()

    with pytest.raises(RuntimeError, match="Text2 required knob state could not be verified"):
        upsert_text2_label(nuke, **_request())

    assert nuke.nodes == []


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


def test_create_then_raise_removes_uniquely_attributable_partial_text2():
    class MutateThenRaiseNuke(FakeNuke):
        def createNode(self, node_class, inpanel=False):
            super().createNode(node_class, inpanel=inpanel)
            raise OSError("host create failed after mutation")

    nuke = MutateThenRaiseNuke()

    with pytest.raises(RuntimeError, match="failed to apply Text2 label layout"):
        upsert_text2_label(nuke, **_request())

    assert nuke.nodes == []


def test_partial_create_fails_closed_when_graph_attribution_is_ambiguous():
    class AmbiguousMutateThenRaiseNuke(FakeNuke):
        def createNode(self, node_class, inpanel=False):
            super().createNode(node_class, inpanel=inpanel)
            self.nodes.append(FakeNode("ConcurrentText", "Text2"))
            raise OSError("host create failed after concurrent mutation")

    nuke = AmbiguousMutateThenRaiseNuke()

    with pytest.raises(RuntimeError, match="Text2 partial creation attribution is ambiguous"):
        upsert_text2_label(nuke, **_request())

    assert len(nuke.nodes) == 2


def test_successful_partial_node_delete_does_not_dereference_invalidated_handle():
    class InvalidatingNode(FakeNode):
        deleted = False

        def name(self):
            if self.deleted:
                raise RuntimeError("invalid node handle")
            return super().name()

    class InvalidatingMismatchNuke(FakeNuke):
        def createNode(self, node_class, inpanel=False):
            node = InvalidatingNode()
            node._knobs["global_font_scale"] = FakeKnob(1.0, ignore_value=0.5)
            self.nodes.append(node)
            return node

        def delete(self, node):
            self.nodes.remove(node)
            node.deleted = True

    nuke = InvalidatingMismatchNuke()

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
    for text in ("[value root.name]", '[python {"REVIEW_MARKER"}]', "escaped\\label", "line\nfeed"):
        with pytest.raises(ValidationError):
            validate(_request(text=text), schema)
    validate(_request(text="镜头 010 — Café 👩‍💻"), schema)


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


def test_tool_route_rejects_expression_shaped_message_before_host_mutation(monkeypatch):
    nuke = FakeNuke()
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    route = _load_tool_route()

    result = route.main.__wrapped__(**_request(text='[python {"REVIEW_MARKER"}]'))

    assert result["success"] is False
    assert result["error"] == "text must be bounded non-executable plain text"
    assert nuke.nodes == []


def test_text_layout_contract_is_in_package_surface_and_documented():
    package = ROOT / "src" / "dcc_mcp_nuke"
    skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert package.joinpath("text_layout.py").exists()
    assert TOOL_ROUTE.exists()
    assert "global_font_scale" in skill_text
    assert "Text2" in skill_text
    assert "finite non-boolean numeric values" in skill_text
    assert "nuke-text-layout" in readme
    assert "finite non-boolean numeric values" in readme
