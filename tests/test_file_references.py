from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from dcc_mcp_nuke.file_references import (
    FileReferenceRuntimeError,
    FileReferenceValidationError,
    convert_file_paths,
    list_file_references,
    path_exists,
    reference_path,
    relink_missing_files,
    remap_path_prefixes,
    sequence_files,
    sequence_pattern,
)

ROOT = Path(__file__).parents[1]
SKILL_ROOT = ROOT / "src" / "dcc_mcp_nuke" / "skills" / "nuke-paths"
RELATIVE = "[file dirname [value root.name]]/"


class FakeKnob:
    def __init__(self, raw, *, evaluated=None, knob_class="File_Knob", animated=False, expression=False, reject=()):
        self.raw = raw
        self.evaluated = evaluated
        self.knob_class = knob_class
        self.animated = animated
        self.expression = expression
        self.reject = set(reject)
        self.writes = []

    def Class(self):
        return self.knob_class

    def getText(self):
        return self.raw

    def evaluate(self):
        return self.raw if self.evaluated is None else self.evaluated

    def isAnimated(self):
        return self.animated

    def hasExpression(self):
        return self.expression

    def setValue(self, value):
        self.writes.append(value)
        if value not in self.reject:
            self.raw = value


class FakeNode:
    def __init__(self, name, knobs, node_class="Read"):
        self._name = name
        self._knobs = knobs
        self._class = node_class

    def fullName(self):
        return self._name

    def Class(self):
        return self._class

    def knobs(self):
        return self._knobs


class FakeRoot:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


class FakeNuke:
    def __init__(self, nodes=(), script="/show/shot/comp.nk"):
        self.nodes = list(nodes)
        self._root = FakeRoot(script)

    def root(self):
        return self._root

    def allNodes(self, *, group, recurseGroups):
        assert group is self._root
        assert recurseGroups is True
        return self.nodes


def _nuke(*paths):
    return FakeNuke([FakeNode(f"Read{index}", {"file": FakeKnob(path)}) for index, path in enumerate(paths)])


def _mappings(old="/old", new="/new"):
    return [{"old_prefix": old, "new_prefix": new}]


def test_discovery_recurses_from_root_and_reads_all_file_knobs(tmp_path):
    source = tmp_path / "plate.exr"
    source.touch()
    expression = "[python get_plate()]"
    nuke = FakeNuke(
        [
            FakeNode("Group1.Camera1", {"file": FakeKnob(expression, evaluated=str(source))}, "Camera2"),
            FakeNode(
                "OCIO1",
                {"lut": FakeKnob("/missing/look.cube"), "label": FakeKnob("x", knob_class="String_Knob")},
                "OCIOFileTransform",
            ),
        ]
    )

    result = list_file_references(nuke)

    assert result["reference_count"] == 2
    reference = result["references"][0]
    assert reference["node"] == "Group1.Camera1"
    assert reference["raw_value"] == expression
    assert reference["evaluated_path"] == str(source)
    assert reference["exists"] is True
    assert reference["expression"] is True
    assert result["references"][1]["exists"] is False


def test_discovery_bounds_output_and_filters_classes():
    nuke = _nuke("/a", "/b", "/c")
    result = list_file_references(nuke, max_items=1)
    assert len(result["references"]) == 1
    assert result["reference_count"] == 3
    assert result["truncated"] is True
    assert list_file_references(nuke, node_classes=["DeepRead"])["reference_count"] == 0


def test_unreadable_expression_is_reported_without_host_details():
    class BrokenKnob(FakeKnob):
        def evaluate(self):
            raise OSError("secret/project")

    nuke = FakeNuke([FakeNode("Read1", {"file": BrokenKnob("[python broken()]")})])
    result = list_file_references(nuke)
    assert result["references"][0]["error"] == "evaluation_failed"
    assert result["references"][0]["exists"] is None
    assert "secret" not in str(result)


@pytest.mark.parametrize("raw", ["plate.%04d.exr", "[value root.project_directory]/plate.####.exr"])
def test_sequence_existence_does_not_depend_on_nukes_evaluated_current_frame(tmp_path, raw):
    (tmp_path / "plate.0001.exr").touch()
    knob = FakeKnob(raw, evaluated=str(tmp_path / "plate.0000.exr"))
    result = list_file_references(FakeNuke([FakeNode("Read1", {"file": knob})]))
    reference = result["references"][0]
    assert reference["evaluated_path"] == str(tmp_path / "plate.0000.exr")
    assert reference["exists"] is True
    assert reference_path(reference) == (tmp_path / Path(raw).name).as_posix()


def test_empty_file_knobs_do_not_require_a_string_from_nuke_evaluate():
    class EmptyKnob(FakeKnob):
        def evaluate(self):
            return None

    result = list_file_references(FakeNuke([FakeNode("Read1", {"proxy": EmptyKnob("")})]))
    reference = result["references"][0]
    assert reference["raw_value"] == reference["evaluated_path"] == ""
    assert reference["exists"] is False
    assert reference["error"] is None


@pytest.mark.parametrize("token", ["%04d", "%4d", "####", "%d"])
def test_sequence_helpers_match_frames_and_capture_frame_number(tmp_path, token):
    for name in ["plate.0001.exr", "plate.10000.exr", "plate.-001.exr", "other.0001.exr"]:
        (tmp_path / name).touch()
    source = str(tmp_path / f"plate.{token}.exr")
    assert path_exists(source) is True
    files = sequence_files(source)
    assert len(files) == 3
    match = sequence_pattern(f"plate.{token}.exr").fullmatch("plate.-001.exr")
    assert match.group("frame") == "-001"
    with pytest.raises(FileReferenceValidationError, match="max_files"):
        sequence_files(source, max_files=2)


def test_sequence_helper_treats_glob_characters_as_literal(tmp_path):
    (tmp_path / "platex.0001.exr").touch()
    assert not path_exists(str(tmp_path / "plate?.####.exr"))
    assert not path_exists(str(tmp_path / "missing" / "plate.####.exr"))


def test_remap_dry_run_directory_boundaries_and_longest_prefix():
    nuke = _nuke("/old/shot/plate.exr", "/older/plate.exr", "/old/a.exr")
    result = remap_path_prefixes(nuke, _mappings() + _mappings("/old/shot", "/shots"))
    assert result["dry_run"] is True
    assert result["change_count"] == 2
    assert result["references"][0]["new_value"] == "/shots/plate.exr"
    assert result["references"][1]["status"] == "no_prefix_match"
    assert all(not node.knobs()["file"].writes for node in nuke.nodes)


def test_remap_only_existing_filters_classes_and_reads_back(tmp_path):
    (tmp_path / "a.exr").touch()
    nuke = _nuke("/old/a.exr", "/old/missing.exr")
    nuke.nodes.append(FakeNode("Write1", {"file": FakeKnob("/old/a.exr")}, "Write"))
    result = remap_path_prefixes(
        nuke, _mappings(new=str(tmp_path)), only_existing=True, node_classes=["Read"], dry_run=False
    )
    assert result["change_count"] == 1
    assert result["references"][0]["readback_value"] == (tmp_path / "a.exr").as_posix()
    assert result["references"][1]["status"] == "destination_missing"
    assert nuke.nodes[-1].knobs()["file"].raw == "/old/a.exr"


def test_remap_preserves_expression_animated_and_unknown_dynamic_state():
    knobs = [
        FakeKnob("/old/[value root.name]"),
        FakeKnob("/old/a.exr", animated=True),
        FakeKnob("/old/a.exr", expression=True),
        FakeKnob("/old/a.exr", animated="unknown"),
    ]
    nuke = FakeNuke([FakeNode(f"Read{i}", {"file": knob}) for i, knob in enumerate(knobs)])
    result = remap_path_prefixes(nuke, _mappings(), dry_run=False)
    assert [item["status"] for item in result["references"]] == ["expression", "animated", "expression", "unreadable"]
    assert not any(knob.writes for knob in knobs)


def test_remap_rolls_back_all_touched_knobs_after_later_readback_failure():
    first = FakeKnob("/old/a.exr")
    second = FakeKnob("/old/b.exr", reject={"/new/b.exr"})
    nuke = FakeNuke([FakeNode("Read1", {"file": first}), FakeNode("Read2", {"file": second})])
    with pytest.raises(FileReferenceRuntimeError, match="all touched knobs were restored"):
        remap_path_prefixes(nuke, _mappings(), dry_run=False)
    assert first.raw == "/old/a.exr"
    assert second.raw == "/old/b.exr"
    assert first.writes == ["/new/a.exr", "/old/a.exr"]


def test_rollback_failure_is_explicit_and_other_knobs_still_restore():
    class BrokenRestore(FakeKnob):
        def setValue(self, value):
            if value == "/old/b.exr":
                raise RuntimeError("secret error")
            super().setValue(value)

    first = FakeKnob("/old/a.exr")
    second = BrokenRestore("/old/b.exr", reject={"/new/b.exr"})
    nuke = FakeNuke([FakeNode("Read1", {"file": first}), FakeNode("Read2", {"file": second})])
    with pytest.raises(FileReferenceRuntimeError, match="rollback could not be verified"):
        remap_path_prefixes(nuke, _mappings(), dry_run=False)
    assert first.raw == "/old/a.exr"


def test_truncated_scan_refuses_batch_edit_before_any_write():
    nuke = _nuke("/old/a.exr", "/old/b.exr")
    with pytest.raises(FileReferenceValidationError, match="exceed max_items"):
        remap_path_prefixes(nuke, _mappings(), max_items=1, dry_run=False)
    assert not any(node.knobs()["file"].writes for node in nuke.nodes)


@pytest.mark.parametrize(
    "script,source",
    [
        ("/show/shot/comp.nk", "/show/shot/media/plate.####.exr"),
        ("P:/show/shot/comp.nk", "P:/show/shot/media/plate.####.exr"),
    ],
)
def test_relative_round_trip_preserves_sequence_token(script, source):
    nuke = FakeNuke([FakeNode("Read1", {"file": FakeKnob(source)})], script=script)
    assert convert_file_paths(nuke, "relative")["references"][0]["new_value"] == RELATIVE + "media/plate.####.exr"
    convert_file_paths(nuke, "relative", dry_run=False)
    assert nuke.nodes[0].knobs()["file"].raw == RELATIVE + "media/plate.####.exr"
    convert_file_paths(nuke, "absolute", dry_run=False)
    assert nuke.nodes[0].knobs()["file"].raw == source


def test_relative_conversion_skips_sibling_paths_and_other_expressions():
    nuke = _nuke("/show/shot-other/a.exr", "[python path()]", "/show/shot/a.exr")
    result = convert_file_paths(nuke, "relative", dry_run=False)
    assert [item["status"] for item in result["references"]] == ["outside_script_folder", "expression", "changed"]
    with pytest.raises(FileReferenceValidationError, match="absolute"):
        convert_file_paths(FakeNuke(script="Root"), "relative")


def test_relink_reports_unique_ambiguous_missing_and_existing(tmp_path):
    (tmp_path / "unique.exr").touch()
    (tmp_path / "duplicate.exr").touch()
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "duplicate.exr").touch()
    nuke = _nuke("/gone/unique.exr", "/gone/duplicate.exr", "/gone/missing.exr", str(tmp_path / "unique.exr"))
    result = relink_missing_files(nuke, [str(tmp_path)])
    assert [item["status"] for item in result["references"]] == [
        "would_change",
        "ambiguous",
        "still_missing",
        "already_exists",
    ]
    assert result["references"][1]["candidate_count"] == 2
    assert result["change_count"] == 1
    assert not nuke.nodes[0].knobs()["file"].writes
    relink_missing_files(nuke, [str(tmp_path)], dry_run=False)
    assert nuke.nodes[0].knobs()["file"].raw == (tmp_path / "unique.exr").as_posix()


def test_relink_groups_sequence_frames_into_one_candidate_and_rejects_partial_search(tmp_path):
    for frame in [1, 2, 3]:
        (tmp_path / f"plate.{frame:04d}.exr").touch()
    nuke = _nuke("/gone/plate.%04d.exr")
    result = relink_missing_files(nuke, [str(tmp_path)])
    assert result["references"][0]["new_value"] == (tmp_path / "plate.%04d.exr").as_posix()
    with pytest.raises(FileReferenceValidationError, match="max_search_files"):
        relink_missing_files(nuke, [str(tmp_path)], max_search_files=2, dry_run=False)
    assert not nuke.nodes[0].knobs()["file"].writes


@pytest.mark.parametrize(
    "kwargs", [{"max_items": True}, {"max_items": 0}, {"node_classes": []}, {"node_classes": ["Read;exec"]}]
)
def test_discovery_validates_direct_python_callers(kwargs):
    with pytest.raises(FileReferenceValidationError):
        list_file_references(FakeNuke(), **kwargs)


@pytest.mark.parametrize(
    "mappings",
    [
        [{"old_prefix": "/old", "new_prefix": "/new/[python bad()]"}],
        [{"old_prefix": "relative", "new_prefix": "/new"}],
        [{"old_prefix": "/old", "new_prefix": "/new", "extra": 1}],
        [],
    ],
)
def test_remap_rejects_malformed_or_executable_prefixes(mappings):
    with pytest.raises(FileReferenceValidationError):
        remap_path_prefixes(_nuke("/old/a.exr"), mappings)


def test_path_skill_validates_and_pairs_writes_with_readback():
    from dcc_mcp_core import validate_skill
    from dcc_mcp_core.verification.lint import find_unpaired_write_verbs

    report = validate_skill(str(SKILL_ROOT))
    assert report.is_clean, [issue.message for issue in report.issues]
    manifest = yaml.safe_load((SKILL_ROOT / "tools.yaml").read_text(encoding="utf-8"))
    assert len(manifest["tools"]) == 4
    assert all(tool["affinity"] == "main" for tool in manifest["tools"])
    assert find_unpaired_write_verbs(manifest["tools"]) == []


def test_path_routes_use_the_shared_contract_and_redact_unexpected_host_errors(monkeypatch):
    monkeypatch.setitem(sys.modules, "nuke", _nuke("/old/a.exr"))
    for name, kwargs in [
        ("list_file_references", {}),
        ("remap_path_prefixes", {"mappings": _mappings()}),
        ("convert_file_paths", {"mode": "relative"}),
    ]:
        spec = importlib.util.spec_from_file_location(name, SKILL_ROOT / "scripts" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.main.__wrapped__(**kwargs)["success"] is True

    class BrokenNuke(FakeNuke):
        def root(self):
            raise RuntimeError("secret/project")

    monkeypatch.setitem(sys.modules, "nuke", BrokenNuke())
    failure = module.main.__wrapped__(mode="relative")
    assert failure["success"] is False
    assert "secret" not in str(failure)


def test_server_registers_path_tools_on_the_main_thread(monkeypatch):
    from dcc_mcp_nuke.dispatcher import NukeDispatcher
    from dcc_mcp_nuke.server import NukeMcpServer

    monkeypatch.setattr(NukeDispatcher, "start", lambda _self: None)
    monkeypatch.setattr(NukeDispatcher, "stop", lambda _self: None)
    server = NukeMcpServer(port=0)
    try:
        server.register_builtin_actions()
        assert server.load_skill("nuke-paths") is True
        action = next(item for item in server.list_actions() if item["name"] == "nuke_paths__remap_path_prefixes")
        assert action["thread_affinity"] == "main"
        assert action["enforce_thread_affinity"] is True
    finally:
        server.stop()
