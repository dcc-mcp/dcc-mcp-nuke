from pathlib import Path

import pytest

from dcc_mcp_nuke.compositing import (
    _apply_layer_adjustments,
    _connect_merge,
    _nuke_path,
    _save_script,
    _set_read_frame_range,
    validate_manifest,
)
from dcc_mcp_nuke.plugin import is_gui_host


def manifest(tmp_path: Path):
    return {
        "script_path": str(tmp_path / "final.nk"),
        "output_path": str(tmp_path / "frames" / "final.%04d.png"),
        "first_frame": 1,
        "last_frame": 10,
        "layers": [
            {"name": "Beauty", "path": str(tmp_path / "beauty.%04d.exr")},
            {"name": "FX", "path": str(tmp_path / "fx.%04d.exr"), "operation": "plus"},
        ],
    }


def test_manifest_normalizes_layer_operations(tmp_path):
    result = validate_manifest(manifest(tmp_path))

    assert result["layers"][0]["operation"] == "over"
    assert result["layers"][1]["operation"] == "plus"
    assert result["layers"][0]["gain"] == 1.0
    assert result["layers"][0]["blur_size"] == 0.0
    assert result["width"] == 1920
    assert result["height"] == 1080


def test_manifest_normalizes_layer_adjustments(tmp_path):
    value = manifest(tmp_path)
    value["layers"][1].update(gain=2.5, blur_size=24)

    result = validate_manifest(value)

    assert result["layers"][1]["gain"] == 2.5
    assert result["layers"][1]["blur_size"] == 24.0


@pytest.mark.parametrize("field", ["gain", "blur_size"])
def test_manifest_rejects_negative_layer_adjustments(tmp_path, field):
    value = manifest(tmp_path)
    value["layers"][0][field] = -1

    with pytest.raises(ValueError, match=f"{field} must be non-negative"):
        validate_manifest(value)


def test_layer_adjustments_are_ordered_gain_then_blur():
    class Knob:
        def __init__(self):
            self.value = None

        def setValue(self, value):
            self.value = value

    class Node:
        def __init__(self, kind):
            self.kind = kind
            self.input = None
            self.node_name = kind
            self._knobs = {"multiply": Knob(), "size": Knob()}

        def setName(self, name):
            self.node_name = name

        def setInput(self, index, node):
            assert index == 0
            self.input = node

        def __getitem__(self, name):
            return self._knobs[name]

        def name(self):
            return self.node_name

    class Nodes:
        def __init__(self):
            self.created = []

        def Grade(self):
            node = Node("Grade")
            self.created.append(node)
            return node

        def Blur(self):
            node = Node("Blur")
            self.created.append(node)
            return node

    class Nuke:
        def __init__(self):
            self.nodes = Nodes()

    nuke = Nuke()
    source = Node("Read")
    result, created = _apply_layer_adjustments(nuke, source, {"gain": 3.0, "blur_size": 18.0}, 1)

    grade, blur = nuke.nodes.created
    assert grade.input is source
    assert grade["multiply"].value == 3.0
    assert blur.input is grade
    assert blur["size"].value == 18.0
    assert result is blur
    assert created == ["Grade_02", "Blur_02"]


def test_manifest_rejects_relative_output(tmp_path):
    value = manifest(tmp_path)
    value["output_path"] = "relative.png"

    with pytest.raises(ValueError, match="output_path must be an absolute path"):
        validate_manifest(value)


def test_only_gui_nuke_processes_start_mcp():
    class Nuke:
        env = {"gui": True}

    class Worker:
        env = {"gui": False}

    assert is_gui_host(Nuke())
    assert not is_gui_host(Worker())


def test_nuke_paths_never_contain_windows_escape_sequences():
    assert _nuke_path(r"C:\artifacts\beauty.%04d.exr") == "C:/artifacts/beauty.%04d.exr"


def test_read_nodes_use_manifest_frame_range():
    class Knob:
        def __init__(self):
            self.value = None

        def setValue(self, value):
            self.value = value

    class Read:
        def __init__(self):
            self._knobs = {name: Knob() for name in ("first", "last", "origfirst", "origlast")}

        def knobs(self):
            return self._knobs

        def __getitem__(self, name):
            return self._knobs[name]

    read = Read()
    _set_read_frame_range(read, 1001, 1100)

    assert {name: knob.value for name, knob in read.knobs().items()} == {
        "first": 1001,
        "last": 1100,
        "origfirst": 1001,
        "origlast": 1100,
    }


def test_merge_connects_existing_comp_as_background_and_new_layer_as_foreground():
    class Merge:
        def __init__(self):
            self.inputs = {}

        def setInput(self, index, node):
            self.inputs[index] = node

    merge = Merge()
    _connect_merge(merge, background="beauty", foreground="information")

    assert merge.inputs == {0: "beauty", 1: "information"}


def test_script_save_is_non_interactive_and_idempotent():
    class Nuke:
        def __init__(self):
            self.saved = None

        def scriptSaveAs(self, path, overwrite):
            self.saved = (path, overwrite)

    nuke = Nuke()
    _save_script(nuke, r"C:\renders\final.nk")

    assert nuke.saved == ("C:/renders/final.nk", 1)
