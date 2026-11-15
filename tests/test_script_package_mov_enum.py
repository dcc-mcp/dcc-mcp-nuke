"""Regression coverage for the GH #110 MOV pixel-format snapshot false positive.

On a cold native evaluation (``fileDependencies`` / ``width``) Nuke clears the
transient menu labels of a MOV Write's pixel-format enumeration without moving
the selected index or setting ``root.modified()``::

    mov64_pixel_format  {0} "yuv420p ..."   ->   {0}   (values() == [])

The opaque scene signature used to see two different strings and refuse
``used``-frame collection with ``scene_changed_since_snapshot``. The fix
collapses both the cold and evaluated serializations to the selected index, but
*only* for a static, recognized Write MOV pixel-format enumeration. A genuine
user change to another pixel format still moves the index and must be rejected.

These tests reproduce the drift fully offline with the same ``Knob``/``Node``
fakes used by the rest of the suite (no live Nuke, no render, no network).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_script_package import Knob, Node, _nuke, _source

from dcc_mcp_nuke import script_package, script_package_scene
from dcc_mcp_nuke.file_references import FileReferenceRuntimeError

# The cold serialization carries the selected index plus host-populated menu
# labels; a native query clears the labels but keeps the index (GH #110).
_MENU = '{0}\n  "yuv420p\\tYCbCr 4:2:0 8-bit"'
_CLEARED = "{0}"
_CHANGED_MENU = '{2}\n  "yuv422p\\tYCbCr 4:2:2 8-bit"'

_DUMMY_NUKE = SimpleNamespace(OutputContext=lambda: object())


class MovPixelFormat(Knob):
    """A MOV Write pixel-format Enumeration_Knob with host-managed menu labels."""

    def __init__(self, raw=_MENU, *, animated=False, expression=False):
        super().__init__(raw, "Enumeration_Knob", animated=animated)
        self._expression = expression

    def getValue(self):
        return 0.0

    def hasExpression(self):
        return self._expression

    def values(self):
        return [] if self.raw == _CLEARED else ["yuv420p\tYCbCr 4:2:0 8-bit"]

    def clear_menu(self):
        """Model the cold native evaluation clearing the transient labels."""
        self.raw = _CLEARED


class Colorspace(Knob):
    """Write colorspace enum whose default label drifts on native evaluation."""

    def __init__(self):
        super().__init__("default", "Enumeration_Knob")

    def getValue(self):
        return 0.0


def _build(tmp_path, mov_knob, mutate, *, extra_knobs=None):
    frame = _source(tmp_path, "plate.0001.exr")
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    nuke.root().setModified(False)
    read = nuke.nodes[0]
    knobs = {"file_type": Knob("mov"), "mov64_pixel_format": mov_knob}
    if extra_knobs:
        knobs.update(extra_knobs)
    write = Node("WriteProbe", knobs, "Write")

    def query(first, last):
        mutate(knobs)
        return [(read, [str(frame)])]

    write.fileDependencies = query
    nuke.nodes.append(write)
    return nuke


# --- The reported false positive is gone -------------------------------------


def test_cold_native_eval_does_not_refuse_used_collection(tmp_path):
    """GH #110: clearing the MOV menu labels must not look like a scene edit."""
    mov = MovPixelFormat()
    nuke = _build(tmp_path, mov, lambda k: k["mov64_pixel_format"].clear_menu())

    result = script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used", dry_run=True)

    assert result["files_count"] >= 1
    assert mov.raw == _CLEARED  # the native query really did clear the labels


def test_colorspace_and_mov_native_drift_are_both_tolerated(tmp_path):
    """0.20.3 colorspace normalization must keep working alongside the MOV fix."""
    mov = MovPixelFormat()
    color = Colorspace()

    def mutate(knobs):
        knobs["colorspace"].raw = "default (sRGB)"
        knobs["mov64_pixel_format"].clear_menu()

    nuke = _build(tmp_path, mov, mutate, extra_knobs={"colorspace": color})

    result = script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used", dry_run=True)

    assert result["files_count"] >= 1


# --- Real user changes are still rejected ------------------------------------


def test_real_pixel_format_index_change_is_still_rejected(tmp_path):
    """A genuine change to another pixel format moves the index and is refused."""
    mov = MovPixelFormat()

    def mutate(knobs):
        knobs["mov64_pixel_format"].raw = _CHANGED_MENU  # user picked a different format

    nuke = _build(tmp_path, mov, mutate)
    with pytest.raises(FileReferenceRuntimeError) as error:
        script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used", dry_run=True)

    assert error.value.context["reason"] == "scene_changed_since_snapshot"
    assert error.value.context["changed_field"] == "node.knob"
    assert error.value.context["knob"] == "mov64_pixel_format"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"animated": True},  # animated enum is not the host-managed drift
        {"expression": True},  # driven by an expression, not a static selection
        {"raw": "custom-user-value"},  # serialization is not an index selection
    ],
)
def test_unexpected_mov_states_are_still_guarded(tmp_path, kwargs):
    """Strict gate: anything outside the known drift keeps its raw signature."""
    mov = MovPixelFormat(**kwargs)
    nuke = _build(tmp_path, mov, lambda k: k["mov64_pixel_format"].clear_menu())

    with pytest.raises(FileReferenceRuntimeError) as error:
        script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used", dry_run=True)

    assert error.value.context["reason"] == "scene_changed_since_snapshot"


# --- Unit-level: the normalization decision itself --------------------------


def _sig_value(name, knob, value, node_class="Write"):
    node = Node("WriteProbe", {}, node_class)
    return script_package_scene._signature_value(_DUMMY_NUKE, node, name, knob, value)


@pytest.mark.parametrize("serialized", [_MENU, _CLEARED])
def test_signature_value_normalizes_cold_and_warm_mov_enum(serialized):
    knob = MovPixelFormat(raw=serialized)
    assert _sig_value("mov64_pixel_format", knob, serialized) == (
        "native_mov_pixel_format",
        "mov64_pixel_format",
        0,
    )


def test_signature_value_distinguishes_a_real_index_change():
    cold = _sig_value("mov64_pixel_format", MovPixelFormat(raw=_MENU), _MENU)
    changed = _sig_value("mov64_pixel_format", MovPixelFormat(raw=_CHANGED_MENU), _CHANGED_MENU)
    assert cold != changed
    assert changed == ("native_mov_pixel_format", "mov64_pixel_format", 2)


@pytest.mark.parametrize(
    "kwargs",
    [{"animated": True}, {"expression": True}, {"raw": "custom-user-value"}],
)
def test_signature_value_keeps_unexpected_states_raw(kwargs):
    knob = MovPixelFormat(**kwargs)
    assert _sig_value("mov64_pixel_format", knob, knob.raw) == knob.raw


def test_signature_value_only_targets_write_mov_pixel_format():
    knob = MovPixelFormat()
    assert _sig_value("mov64_pixel_format", knob, _MENU, node_class="Read") == _MENU
    assert _sig_value("mov64_codec", knob, _MENU) == _MENU
    assert _sig_value("mov64_pixel_format", Knob(_MENU), _MENU) == _MENU  # not an Enumeration_Knob


@pytest.mark.parametrize("label", ["default", "default (sRGB)"])
def test_signature_value_still_normalizes_colorspace_default_label(label):
    color = Colorspace()
    color.raw = label
    assert _sig_value("colorspace", color, label) == ("native_write_default_colorspace", 0)


# --- Unsupported serialization shapes stay raw (GH #110 guard hardening) -----


@pytest.mark.parametrize(
    "raw",
    [
        "changed-before {0}",  # text before the index token
        "{0} changed-after",  # unquoted trailing text
        "{0} {2}",  # a second index token
    ],
)
def test_unsupported_mov_serialization_shapes_stay_raw(raw):
    """Only a complete index (+ quoted menu labels) serialization is normalized."""
    knob = MovPixelFormat(raw=raw)
    assert _sig_value("mov64_pixel_format", knob, raw) == raw


def test_same_index_malformed_content_change_stays_detectable():
    """A real content change at a fixed index must not collapse to one token."""
    before = "{0} original-user-content"
    after = "{0} changed-user-content"
    knob = MovPixelFormat()
    before_sig = _sig_value("mov64_pixel_format", knob, before)
    after_sig = _sig_value("mov64_pixel_format", knob, after)
    assert before_sig == before
    assert after_sig == after
    assert before_sig != after_sig


def test_cold_menu_with_multiple_labels_is_normalized():
    """A full cold menu (index + several quoted labels) is still the GH #110 shape."""
    raw = '{0}\n  "yuv420p\\tYCbCr 4:2:0 8-bit"\n  "yuv422p\\tYCbCr 4:2:2 8-bit"'
    knob = MovPixelFormat(raw=raw)
    assert _sig_value("mov64_pixel_format", knob, raw) == (
        "native_mov_pixel_format",
        "mov64_pixel_format",
        0,
    )


def test_malformed_same_index_content_change_is_still_rejected(tmp_path):
    """The scene guard must still refuse a real same-index content change."""
    mov = MovPixelFormat()

    def mutate(knobs):
        knobs["mov64_pixel_format"].raw = "{0} changed-user-content"

    nuke = _build(tmp_path, mov, mutate)
    with pytest.raises(FileReferenceRuntimeError) as error:
        script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used", dry_run=True)
    assert error.value.context["reason"] == "scene_changed_since_snapshot"
