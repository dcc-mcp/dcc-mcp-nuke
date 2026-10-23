"""Internal SDK snapshots must not perform filesystem existence probes."""

from types import SimpleNamespace

import pytest

from dcc_mcp_nuke import file_references


@pytest.mark.parametrize("raw", ["/show/plate.exr", ""])
def test_sdk_only_snapshot_does_not_check_filesystem(monkeypatch, raw):
    def unexpected_probe(_path):
        pytest.fail("SDK-only snapshot accessed the filesystem")

    monkeypatch.setattr(file_references, "path_exists", unexpected_probe)
    knob = SimpleNamespace(
        Class=lambda: "File_Knob",
        getText=lambda: raw,
        evaluate=lambda: raw,
        hasExpression=lambda: False,
        isAnimated=lambda: False,
    )
    node = SimpleNamespace(fullName=lambda: "Read1", Class=lambda: "Read", knobs=lambda: {"file": knob})
    nuke = SimpleNamespace(root=lambda: object(), allNodes=lambda **_kwargs: [node])
    record = file_references._describe(node, "Read", "file", knob, include_exists=False)
    assert record["evaluated_path"] == raw
    assert record["exists"] is None and record["error"] is None
    if raw:
        records, count = file_references._references(nuke, 1, None, include_exists=False)
        assert count == 1 and records[0][0] == record


def test_internal_snapshot_checks_existence_by_default(monkeypatch):
    checked = []
    monkeypatch.setattr(file_references, "path_exists", lambda path: checked.append(path) or True)
    knob = SimpleNamespace(
        getText=lambda: "/show/plate.exr",
        evaluate=lambda: "/expanded/plate.exr",
        hasExpression=lambda: False,
        isAnimated=lambda: False,
    )
    node = SimpleNamespace(fullName=lambda: "Read1")
    record = file_references._describe(node, "Read", "file", knob)
    assert record["exists"] is True and checked == ["/expanded/plate.exr"]
