from __future__ import annotations

import pytest
from test_script_package import Knob, Node, _nuke, _source

from dcc_mcp_nuke import script_package, script_package_scene
from dcc_mcp_nuke.file_references import FileReferenceRuntimeError


class DefaultColorspace(Knob):
    def __init__(self):
        super().__init__("default", "Enumeration_Knob")
        self.index = 0.0

    def getValue(self):
        return self.index


class CheckHash(Knob):
    def __init__(self):
        super().__init__("true", "Boolean_Knob")

    def value(self):
        return self.raw == "true"


def _cold_write(nuke):
    color, check = DefaultColorspace(), CheckHash()
    write = Node("Write1", {"colorspace": color, "checkHashOnRead": check}, "Write")
    nuke.nodes.append(write)
    return write, color, check


def test_cold_native_query_preserves_hash_setting_and_accepts_only_derived_default_label(tmp_path):
    frame = _source(tmp_path, "plate.0001.exr")
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    nuke.root().setModified(False)
    write, color, check = _cold_write(nuke)
    calls = []

    def query(first, last):
        assert first == last and check.value() is True
        calls.append(first)
        color.raw = "default (sRGB)"
        check.raw = "false"
        return [(nuke.nodes[0], [str(frame)])]

    write.fileDependencies = query

    result = script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used")

    assert calls == [1, 2] and result["files_count"] == 1
    assert check.value() is True and nuke.Undo.depth == 0
    assert nuke.root().modified() is False


@pytest.mark.parametrize("change", ["explicit_color", "animated", "expression", "views", "unknown_label", "viewer"])
def test_known_query_side_effect_does_not_hide_real_scene_drift(tmp_path, change):
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    write, color, check = _cold_write(nuke)
    viewer = Node("Viewer1", {}, "Viewer")
    nuke.nodes.append(viewer)

    def query(*_args):
        color.raw = "default (sRGB)"
        check.raw = "false"
        if change == "explicit_color":
            color.index = 2.0
        elif change == "animated":
            color.animated = True
        elif change == "expression":
            color.hasExpression = lambda: True
        elif change == "views":
            color.toScript = lambda _quote=False, context=None: "default (sRGB)" if context else "left right"
        elif change == "unknown_label":
            color.raw = "default-user-value"
        else:
            viewer.inputs = lambda: 1
            viewer.input = lambda _index: write
        return []

    write.fileDependencies = query
    with pytest.raises(FileReferenceRuntimeError) as error:
        script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used")
    assert error.value.context["reason"] == "scene_changed_since_snapshot"
    assert check.value() is True and not nuke.saves


def test_hash_setting_is_restored_even_when_native_query_raises(tmp_path):
    nuke = _nuke(tmp_path)
    write, _color, check = _cold_write(nuke)

    def query(*_args):
        check.raw = "false"
        raise RuntimeError("native failure")

    write.fileDependencies = query
    with pytest.raises(RuntimeError, match="native failure"):
        script_package_scene._query_dependencies(nuke, write, 1)
    assert check.value() is True and nuke.Undo.depth == 0


def test_failed_hash_rollback_is_actionable(tmp_path):
    nuke = _nuke(tmp_path)
    write, _color, check = _cold_write(nuke)
    write.fileDependencies = lambda *_args: setattr(check, "raw", "false") or []
    check.fromScript = lambda _value: None
    with pytest.raises(FileReferenceRuntimeError) as error:
        script_package_scene._query_dependencies(nuke, write, 1)
    assert error.value.context == {
        "reason": "dependency_state_restore_failed",
        "node": "Write1",
        "knob": "checkHashOnRead",
    }
    assert nuke.Undo.depth == 0


def test_unique_file_budget_reports_limit_before_filesystem_scan(tmp_path, monkeypatch):
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    read = nuke.nodes[0]
    nuke.nodes.append(Node("Write1", {}, "Write", dependencies=[(read, ["one.exr", "two.exr"])]))
    monkeypatch.setattr(script_package, "sequence_files", lambda *_a, **_kw: pytest.fail("limited proof was scanned"))
    with pytest.raises(FileReferenceRuntimeError) as error:
        script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used", max_files=1)
    assert error.value.context["reason"] == "dependency_query_limit"
    assert error.value.context["limit"] == "files"
    assert error.value.context["query_node"] == "Write1" and error.value.context["query_frame"] == 1
    assert not (tmp_path / "package").exists()
