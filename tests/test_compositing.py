from pathlib import Path

import pytest

from dcc_mcp_nuke.compositing import _nuke_path, _set_read_frame_range, validate_manifest
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
    assert result["width"] == 1920
    assert result["height"] == 1080


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
