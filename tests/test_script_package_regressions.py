"""Regression coverage for the `used` frame-mode follow-up fixes.

Two host-reported failures are reproduced without a live Nuke:

* On Windows, a mixed-case sequence basename was matched with a case-sensitive
  regex after the native dependency paths had been folded with
  ``os.path.normcase``, producing a spurious ``dependency_path_mismatch``.
* A ``scene_changed_since_snapshot`` refusal is opaque; the drift diagnostic
  must pinpoint the exact signature field that changed so cold-query side
  effects can be located on the real host.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from test_script_package import Node, _nuke, _source

from dcc_mcp_nuke import script_package, script_package_scene
from dcc_mcp_nuke.file_references import FileReferenceRuntimeError, sequence_pattern


def _simulate_windows_normcase(monkeypatch):
    """Make os.path.normcase fold case like Windows, even on POSIX CI."""
    monkeypatch.setattr(script_package.os.path, "normcase", lambda path: path.lower())


def _simulate_posix_normcase(monkeypatch):
    """Force an identity normcase so case stays significant on any host."""
    monkeypatch.setattr(script_package.os.path, "normcase", lambda path: path)


# --- Windows case-folding: dependency_path_mismatch --------------------------


def test_minimal_repro_folds_case_on_both_pattern_and_candidate(monkeypatch):
    """The #110 repro: fold case on both sides, not just the candidate."""
    _simulate_windows_normcase(monkeypatch)
    folded = script_package.os.path.normcase("SHOT_BG_OCC.0101.exr")

    naive = sequence_pattern("SHOT_BG_OCC.%04d.exr")
    assert naive.fullmatch("SHOT_BG_OCC.0101.exr") is not None
    assert naive.fullmatch(folded) is None  # the old, asymmetric comparison

    consistent = sequence_pattern(script_package.os.path.normcase("SHOT_BG_OCC.%04d.exr"))
    assert consistent.fullmatch(folded) is not None  # the fix


def test_used_reference_paths_keeps_case_significant_when_host_does(tmp_path, monkeypatch):
    """On a case-sensitive host a differently-cased candidate must be rejected."""
    _simulate_posix_normcase(monkeypatch)
    source = str(tmp_path / "SHOT_BG_OCC.%04d.exr")
    different_case = str(tmp_path / "shot_bg_occ.0101.exr")
    scene = SimpleNamespace(first=1, last=2)
    record = {"node": "Read1", "class": "Read", "knob": "file"}

    with pytest.raises(FileReferenceRuntimeError) as error:
        script_package._used_reference_paths(scene, record, source, {different_case})

    assert error.value.context["reason"] == "dependency_path_mismatch"


def test_used_reference_paths_folds_case_on_windows_and_returns_original_path(tmp_path, monkeypatch):
    """A differently-cased native basename still matches on Windows, unchanged.

    On a case-insensitive host the knob may say ``SHOT_BG_OCC`` while the native
    dependency query reports ``shot_bg_occ``. Identity is compared case-folded on
    both sides, but the path handed to real file I/O is returned untouched.
    """
    _simulate_windows_normcase(monkeypatch)
    source = str(tmp_path / "SHOT_BG_OCC.%04d.exr")
    frame = str(tmp_path / "shot_bg_occ.0101.exr")
    scene = SimpleNamespace(first=1, last=2)
    record = {"node": "Read1", "class": "Read", "knob": "file"}

    result = script_package._used_reference_paths(scene, record, source, {frame})

    assert result == [frame]  # original case, ready for real file I/O


def test_used_mode_matches_mixed_case_paths_and_preserves_original_case(tmp_path, monkeypatch):
    """End-to-end: no dependency_path_mismatch, and the copied file keeps its case."""
    _simulate_windows_normcase(monkeypatch)
    frame = _source(tmp_path, "SHOT_BG_OCC.0101.exr")
    nuke = _nuke(tmp_path, tmp_path / "SHOT_BG_OCC.%04d.exr")
    read = nuke.nodes[0]
    write = Node("Write1", {}, "Write")
    write.fileDependencies = lambda first, last: [(read, [str(frame)])]
    nuke.nodes.append(write)

    result = script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used")

    assert [Path(item["source"]).name for item in result["files"]] == ["SHOT_BG_OCC.0101.exr"]
    assert result["missing_count"] == 0


# --- Locating the changed scene-signature field ------------------------------

_ROOT = ("script.nk", False, 1, 2, 101, ())
_NODE = ("Read1", "Read", (("file", "/media/plate.%04d.exr"),), ())


def _sig(root=_ROOT, *nodes):
    return (root, *nodes)


def _baseline():
    return _sig(_ROOT, _NODE)


def _replace_root_field(index, value):
    root = list(_ROOT)
    root[index] = value
    return _sig(tuple(root), _NODE)


def _replace_knob(name, value):
    knobs = dict(_NODE[2])
    knobs[name] = value
    return _sig(_ROOT, (_NODE[0], _NODE[1], tuple(knobs.items()), _NODE[3]))


@pytest.mark.parametrize(
    "before, after, expected",
    [
        (None, _baseline(), {"changed_field": "signature_unavailable"}),
        (_baseline(), _baseline(), {"changed_field": "unknown"}),
        (_baseline(), _replace_root_field(1, True), {"changed_field": "root.modified"}),
        (_baseline(), _replace_root_field(4, 102), {"changed_field": "root.frame"}),
        (_baseline(), _replace_root_field(5, ("new/plugin",)), {"changed_field": "root.pluginPath"}),
        (
            _baseline(),
            _replace_knob("file", "/media/other.%04d.exr"),
            {"changed_field": "node.knob", "node": "Read1", "knob": "file"},
        ),
        (
            _baseline(),
            _sig(_ROOT, ("Read1", "Write", _NODE[2], _NODE[3])),
            {"changed_field": "node.class", "node": "Read1"},
        ),
        (
            _baseline(),
            _sig(_ROOT, ("Renamed1", _NODE[1], _NODE[2], _NODE[3])),
            {"changed_field": "node.name", "node": "Read1"},
        ),
        (
            _baseline(),
            _sig(_ROOT, (_NODE[0], _NODE[1], _NODE[2], ("Read2",))),
            {"changed_field": "node.inputs", "node": "Read1"},
        ),
        (_baseline(), _sig(_ROOT, _NODE, _NODE), {"changed_field": "node_count"}),
    ],
)
def test_describe_scene_change_pinpoints_the_field(before, after, expected):
    assert script_package_scene.describe_scene_change(before, after) == expected


def test_describe_scene_change_never_leaks_knob_values():
    result = script_package_scene.describe_scene_change(
        _baseline(), _replace_knob("file", "S:/private/secret_shot.%04d.exr")
    )
    assert result == {"changed_field": "node.knob", "node": "Read1", "knob": "file"}
    assert "secret_shot" not in str(result)


def test_describe_scene_change_locates_real_signature_knob_drift(tmp_path):
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    read = nuke.nodes[0]
    before = script_package_scene.scene_signature(nuke, 1024)
    read.knobs()["file"].raw = "changed.%04d.exr"
    after = script_package_scene.scene_signature(nuke, 1024)

    assert script_package_scene.describe_scene_change(before, after) == {
        "changed_field": "node.knob",
        "node": read.fullName(),
        "knob": "file",
    }


def test_scene_changed_error_carries_field_diagnostic(tmp_path, monkeypatch):
    """The refusal now reports which field moved, not just the bare reason."""
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    read = nuke.nodes[0]
    nuke.nodes.append(Node("Write1", {}, "Write"))
    original = script_package_scene.on_host_thread
    drifted = []

    def dispatch(host, callback):
        result = original(host, callback)
        if not drifted:
            drifted.append(True)
            read.knobs()["file"].raw = "changed.%04d.exr"
        return result

    monkeypatch.setattr(script_package_scene, "on_host_thread", dispatch)
    with pytest.raises(FileReferenceRuntimeError) as error:
        script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used")

    assert error.value.context["reason"] == "scene_changed_since_snapshot"
    assert error.value.context["changed_field"] == "node.knob"
    assert error.value.context["knob"] == "file"
