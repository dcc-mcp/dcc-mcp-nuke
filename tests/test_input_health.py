import pytest

from dcc_mcp_nuke.input_health import check_input_health


class Knob:
    def __init__(self, value, file=False):
        self.current = value
        self.file = file

    def value(self):
        return self.current

    getValue = value
    getText = value
    evaluate = value

    def Class(self):
        return "File_Knob" if self.file else "Int_Knob"

    def hasExpression(self):
        return False

    def isAnimated(self):
        return False


class Node:
    def __init__(self, name, node_class="Read", **knobs):
        self._name = name
        self._class = node_class
        self._knobs = {key: Knob(value, file=key == "file") for key, value in knobs.items()}

    def fullName(self):
        return self._name

    name = fullName

    def Class(self):
        return self._class

    def knobs(self):
        return self._knobs


class Nuke:
    ALL = 1
    REGISTERED = 2

    def __init__(self, nodes, first=1, last=5):
        self.nodes = nodes
        self._root = Node("shot.nk", "Root", first_frame=first, last_frame=last)

    def root(self):
        return self._root

    def allNodes(self, **kwargs):
        assert kwargs == {"group": self._root, "recurseGroups": True}
        return self.nodes

    def plugins(self, flags):
        assert flags == 3
        return ["/plugins/Blur.dll", "RegisteredBundleOp", "OFX.com.vendor.effect"]


def _codes(result, severity):
    return {issue["code"]: issue for issue in result["issues"][severity]}


def test_missing_gaps_zero_small_and_ranges_are_grouped(tmp_path):
    for frame, size in [(1, 100), (2, 1), (3, 100), (5, 0)]:
        (tmp_path / f"beauty.{frame:04d}.exr").write_bytes(b"x" * size)
    read = Node("Group.Read", file=str(tmp_path / "beauty.%04d.exr"), first=1, last=4)
    result = check_input_health(Nuke([read]))

    assert _codes(result, "error")["missing_frames"]["frame_ranges"] == [[4, 4]]
    warnings = _codes(result, "warning")
    assert warnings["range_mismatch"]["node_range"] == [1, 4]
    assert warnings["zero_byte_frames"]["frame_ranges"] == [[5, 5]]
    assert warnings["small_frames"]["frame_ranges"] == [[2, 2]]
    assert result["checked_references"] == 1
    assert result["truncated"] is False
    assert read.knobs()["file"].value() == str(tmp_path / "beauty.%04d.exr")


def test_missing_files_and_write_outputs_are_distinguished(tmp_path):
    result = check_input_health(
        Nuke([Node("Geo", "ReadGeo2", file=str(tmp_path / "missing.abc")), Node("Output", "Write", file="missing.png")])
    )
    assert result["checked_references"] == 1
    assert _codes(result, "error")["missing"]["node"] == "Geo"


def test_write_range_and_read_range_gaps_include_outside_root(tmp_path):
    (tmp_path / "image.0001.exr").write_bytes(b"image")
    read = Node("Read", file=str(tmp_path / "image.####.exr"), first=1, last=2)
    write = Node("Write", "Write", use_limit=True, first=3, last=4)
    result = check_input_health(Nuke([read, write], last=1), include_write_ranges=True)
    assert result["write_ranges"] == [[3, 4]]
    assert _codes(result, "error")["missing_frames"]["frame_ranges"] == [[2, 4]]


def test_sequence_preserves_token_when_evaluate_expands_current_frame(tmp_path):
    (tmp_path / "image.0001.exr").write_bytes(b"image")
    read = Node("Read", file=str(tmp_path / "image.####.exr"), first=1, last=2)
    read.knobs()["file"].evaluate = lambda: str(tmp_path / "image.0001.exr")
    result = check_input_health(Nuke([read], last=2))
    assert _codes(result, "error")["missing_frames"]["frame_ranges"] == [[2, 2]]


def test_unknown_nodes_and_required_plugins_are_checked_without_creating_nodes():
    result = check_input_health(
        Nuke([Node("Unresolved", "Unknown")]),
        required_node_classes=["Blur", "RegisteredBundleOp", "OFX.com.vendor.effect", "AbsentOFX"],
    )
    errors = _codes(result, "error")
    assert errors["unknown_node_class"]["node"] == "Unresolved"
    assert errors["missing_plugin"]["node_class"] == "AbsentOFX"
    assert result["required_classes_not_found"] == ["AbsentOFX"]


def test_plugin_inventory_failure_is_unverified():
    nuke = Nuke([])
    nuke.plugins = lambda _flags: (_ for _ in ()).throw(RuntimeError("private detail"))
    result = check_input_health(nuke, required_node_classes=["Absent"])
    assert result["plugin_inventory_available"] is False
    assert "missing_plugin" not in _codes(result, "error")
    assert "unverified_plugin" in _codes(result, "warning")
    assert "private detail" not in str(result)


def test_limits_explicitly_report_incomplete_checks(tmp_path):
    (tmp_path / "image.0001.exr").write_bytes(b"image")
    read = Node("Read", file=str(tmp_path / "image.%04d.exr"))
    result = check_input_health(Nuke([read], last=100), max_frames=2)
    assert "frame_limit_exceeded" in _codes(result, "warning")
    result = check_input_health(Nuke([Node("A", file="missing.a"), Node("B", file="missing.b")]), max_issues=1)
    assert result["issue_count"] == 2
    assert result["truncated"] is True
    assert len(result["issues"]["error"]) == 1


def test_directory_scan_limit_is_not_reported_as_a_missing_file(tmp_path):
    for frame in range(3):
        (tmp_path / f"image.{frame:04d}.exr").touch()
    result = check_input_health(Nuke([Node("Read", file=str(tmp_path / "image.%04d.exr"))]), max_files=1)
    assert "file_check_incomplete" in _codes(result, "warning")
    assert not result["issues"]["error"]


@pytest.mark.parametrize(
    "options",
    [
        {"max_frames": True},
        {"max_issues": 0},
        {"small_frame_ratio": float("nan")},
        {"required_node_classes": 0},
        {"include_write_ranges": "yes"},
    ],
)
def test_invalid_options_rejected(options):
    with pytest.raises(ValueError):
        check_input_health(Nuke([]), **options)


def test_frame_list_compression_is_bounded(tmp_path):
    (tmp_path / "image.0000.exr").write_bytes(b"image")
    for frame in range(2, 150, 2):
        (tmp_path / f"image.{frame:04d}.exr").write_bytes(b"image")
    result = check_input_health(Nuke([Node("Read", file=str(tmp_path / "image.%04d.exr"))], first=0, last=149))
    gap = _codes(result, "error")["missing_frames"]
    assert gap["frame_count"] == 75
    assert len(gap["frame_ranges"]) == 64
    assert gap["ranges_truncated"] is True
