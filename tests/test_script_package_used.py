from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from dcc_mcp_core.cancellation import CancelToken, DccMcpCancelledError, reset_cancel_token, set_cancel_token
from test_script_package import Knob, Node, _nuke, _source

from dcc_mcp_nuke import script_package, script_package_scene
from dcc_mcp_nuke.file_references import FileReferenceError, FileReferenceRuntimeError


@pytest.mark.parametrize("branch", ["parked", "disabled_mask"])
@pytest.mark.parametrize("dry_run", [True, False])
def test_complete_output_queries_report_absent_read_without_scanning_or_rewriting(
    tmp_path, monkeypatch, branch, dry_run
):
    plate = _source(tmp_path, "plate.0101.exr")
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    read = nuke.nodes[0]
    if branch == "disabled_mask":
        grade = Node("DisabledGrade", {"disable": Knob("true", "Boolean_Knob")}, "Grade")
        grade.inputs = lambda: 2
        grade.input = lambda index: read if index == 1 else None
        nuke.nodes.append(grade)
    calls = []
    for name in ("WriteA", "WriteB"):
        write = Node(name, {}, "Write")
        write.fileDependencies = lambda first, last, name=name: calls.append((name, first, last)) or []
        nuke.nodes.append(write)
    monkeypatch.setattr(script_package, "sequence_files", lambda *_a, **_kw: pytest.fail("unused Read was scanned"))
    original = read.knobs()["file"].toScript()

    result = script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used", dry_run=dry_run)

    assert calls == [(name, frame, frame) for name in ("WriteA", "WriteB") for frame in (1, 2)]
    assert result["files_count"] == result["rewrite_count"] == result["missing_count"] == 0
    assert result["unused_references"] == [
        {
            "node": read.fullName(),
            "class": "Read",
            "knob": "file",
            "path": original.replace("\\", "/"),
            "reason": "not_reported_by_outputs",
        }
    ]
    assert result["unused_references_count"] == 1
    assert read.knobs()["file"].toScript() == original
    assert read.knobs()["file"].writes == []
    assert plate.read_bytes() == b"pixels"
    if not dry_run:
        inspected = script_package.inspect_package(str(tmp_path / "package"))
        assert inspected["unused_references"] == result["unused_references"]


@pytest.mark.parametrize("unsafe", ["other_class", "multiple_contexts", "expression", "animated", "no_outputs"])
def test_absence_is_not_permission_to_omit_an_ambiguous_reference(tmp_path, unsafe):
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    read = nuke.nodes[0]
    knob = read.knobs()["file"]
    if unsafe == "other_class":
        read.node_class = "ThirdPartyRead"
    elif unsafe == "multiple_contexts":
        knob.toScript = lambda _quote=False, context=None: knob.raw + (" right_view" if context is None else "")
    elif unsafe == "expression":
        knob.hasExpression = lambda: True
    elif unsafe == "animated":
        knob.animated = True
    if unsafe != "no_outputs":
        nuke.nodes.append(Node("Write1", {}, "Write"))

    with pytest.raises(FileReferenceRuntimeError) as error:
        script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used")

    assert error.value.context["reason"] == "dependency_not_reported"
    assert not nuke.saves


def test_single_frame_queries_union_native_time_remaps_across_outputs(tmp_path):
    frames = {number: _source(tmp_path, f"plate.{number:04d}.exr") for number in (9, 10, 20)}
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    read = nuke.nodes[0]
    calls = []
    for name, needed in (("WriteA", {1: [9], 2: [9, 10]}), ("WriteB", {1: [20], 2: [20]})):
        write = Node(name, {}, "Write")

        def query(first, last, name=name, needed=needed):
            assert first == last
            calls.append((name, first))
            return [(read, [str(frames[number]) for number in needed[first]])]

        write.fileDependencies = query
        nuke.nodes.append(write)

    result = script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used", max_files=3)

    assert calls == [(name, frame) for name in ("WriteA", "WriteB") for frame in (1, 2)]
    assert {Path(item["source"]) for item in result["files"]} == set(frames.values())


def test_each_native_query_returns_to_host_queue_and_keeps_sdk_handles_off_worker(tmp_path, monkeypatch):
    frame = _source(tmp_path, "plate.0001.exr")
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    read = nuke.nodes[0]
    write = Node("Write1", {}, "Write")
    nuke.nodes.append(write)
    calls, events, sdk_threads, filesystem_threads = [], [], [], []
    with ThreadPoolExecutor(max_workers=1) as host, ThreadPoolExecutor(max_workers=1) as worker:
        host_id = host.submit(threading.get_ident).result()

        def query(first, last):
            assert threading.get_ident() == host_id
            assert first == last
            calls.append(first)
            events.append(f"query{first}")
            return [(read, [str(frame)])]

        write.fileDependencies = query

        def observe(method):
            def call(*args, **kwargs):
                sdk_threads.append(threading.get_ident())
                assert threading.get_ident() == host_id
                return method(*args, **kwargs)

            return call

        for obj in (nuke, nuke._root, *nuke.nodes, *read.knobs().values()):
            for name in dir(obj):
                method = getattr(obj, name)
                if not name.startswith("_") and callable(method):
                    monkeypatch.setattr(obj, name, observe(method))

        def dispatch(callback):
            before = len(calls)
            result = host.submit(callback).result(timeout=3)
            assert len(calls) - before <= 1, "multiple SDK queries monopolized one host callback"
            if len(calls) > before:
                assert host.submit(lambda: events.append("ping") or 42).result(timeout=0.5) == 42
            return result

        nuke.executeInMainThreadWithResult = dispatch
        original_stat = Path.stat

        def stat(path, *args, **kwargs):
            filesystem_threads.append(threading.get_ident())
            assert threading.get_ident() != host_id
            return original_stat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", stat)
        result = worker.submit(script_package.package_script, nuke, str(tmp_path / "package"), frame_mode="used")
        assert result.result(timeout=5)["files_count"] == 1
        assert events == ["query1", "ping", "query2", "ping"]
        assert set(sdk_threads) == {host_id}
        assert filesystem_threads and host_id not in filesystem_threads


@pytest.mark.parametrize("interruption", ["cancel", "drift"])
def test_between_frame_checkpoint_prevents_more_sdk_work_or_publication(tmp_path, monkeypatch, interruption):
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    calls = []
    write = Node("Write1", {}, "Write")
    write.fileDependencies = lambda first, last: calls.append((first, last)) or []
    nuke.nodes.append(write)
    with ThreadPoolExecutor(max_workers=1) as host, ThreadPoolExecutor(max_workers=1) as worker:

        def dispatch(callback):
            result = host.submit(callback).result(timeout=3)
            if calls and interruption == "drift":
                host.submit(lambda: nuke.root().setName("new-user-script.nk")).result(timeout=3)
            return result

        def cancelled():
            if calls and interruption == "cancel":
                raise RuntimeError("owned cancellation")

        nuke.executeInMainThreadWithResult = dispatch
        monkeypatch.setattr(script_package_scene, "check_cancelled", cancelled, raising=False)
        result = worker.submit(script_package.package_script, nuke, str(tmp_path / "package"), frame_mode="used")
        with pytest.raises((RuntimeError, FileReferenceRuntimeError)) as error:
            result.result(timeout=5)
        if interruption == "drift":
            assert error.value.context["reason"] == "scene_changed_since_snapshot"
            assert nuke.root().name() == "new-user-script.nk"
        assert calls == [(1, 1)]
        assert not nuke.saves and not (tmp_path / "package").exists()


@pytest.mark.parametrize("invalid", [None, "not rows", [None], [("not a node", [])], "unknown_node"])
def test_failure_in_later_query_never_turns_partial_coverage_into_unused(tmp_path, invalid):
    frame = _source(tmp_path, "plate.0001.exr")
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr", tmp_path / "parked.%04d.exr")
    read = nuke.nodes[0]
    nuke.nodes.append(Node("WriteA", {}, "Write", dependencies=[(read, [str(frame)])]))
    write = Node("WriteB", {}, "Write")
    if invalid == "unknown_node":
        invalid = [(Node("NotInTheScene", {}, "Read"), [str(frame)])]
    write.fileDependencies = lambda *_args: invalid
    nuke.nodes.append(write)

    with pytest.raises(FileReferenceRuntimeError) as error:
        script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used")

    assert error.value.context["reason"] == "dependency_query_failed"
    assert error.value.context["query_node"] == "WriteB"
    assert not nuke.saves and not (tmp_path / "package").exists()


@pytest.mark.parametrize("root_row", ["scene_root", "root_class_node"])
def test_used_mode_tolerates_a_root_dependency_row_from_a_writer(tmp_path, root_row):
    # Repro of #110: a generator -> Write script has no Read nodes, yet
    # Write.fileDependencies() reports a Root-class row (the OCIO config path
    # plus the .nk script itself). Root is absent from nuke.allNodes(), so the
    # row must be safely skipped rather than failing the dependency proof.
    nuke = _nuke(tmp_path)
    ramp = Node("Ramp1", {}, "Ramp")
    write = Node("Write1", {}, "Write")
    write.inputs = lambda: 1
    write.input = lambda index: ramp if index == 0 else None
    root_node = nuke.root() if root_row == "scene_root" else Node("root", {}, "Root")
    host_paths = [str(tmp_path / "config.ocio"), nuke.root().fullName()]

    def query(first, last):
        assert first == last
        return [(root_node, host_paths)]

    write.fileDependencies = query
    nuke.nodes = [ramp, write]

    result = script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used")

    assert result["frame_mode"] == "used"
    assert result["files_count"] == result["missing_count"] == result["unused_references_count"] == 0
    assert not nuke.saves


def test_root_dependency_row_does_not_hide_real_read_dependencies(tmp_path):
    _source(tmp_path, "plate.0001.exr")
    frame = _source(tmp_path, "plate.0009.exr")
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    read = nuke.nodes[0]
    write = Node("Write1", {}, "Write")
    host_paths = [str(tmp_path / "config.ocio"), nuke.root().fullName()]

    def query(first, last):
        assert first == last
        return [(nuke.root(), host_paths), (read, [str(frame)])]

    write.fileDependencies = query
    nuke.nodes.append(write)

    result = script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used")

    assert [Path(item["source"]).name for item in result["files"]] == ["plate.0009.exr"]
    assert result["missing_count"] == result["unused_references_count"] == 0


def test_a_read_reported_by_a_later_output_is_included_after_all_queries(tmp_path):
    frame = _source(tmp_path, "plate.0010.exr")
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    read = nuke.nodes[0]
    nuke.nodes.append(Node("WriteA", {}, "Write"))
    nuke.nodes.append(Node("WriteB", {}, "Write", dependencies=[(read, [str(frame)])]))

    result = script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used")

    assert [Path(item["source"]) for item in result["files"]] == [frame]
    assert result["unused_references_count"] == 0


@pytest.mark.parametrize("change", ["knob", "proxy", "frame", "topology", "plugins"])
def test_each_frame_is_guarded_against_scene_drift(tmp_path, monkeypatch, change):
    frame = _source(tmp_path, "plate.0001.exr")
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    read = nuke.nodes[0]
    proxy = Knob("false", "Boolean_Knob")
    nuke.root().knobs()["proxy"] = proxy
    calls = []
    write = Node("Write1", {}, "Write")
    write.fileDependencies = lambda first, last: calls.append(first) or [(read, [str(frame)])]
    nuke.nodes.append(write)
    original = script_package_scene.on_host_thread

    def dispatch(host, callback):
        result = original(host, callback)
        if calls:
            if change == "knob":
                read.knobs()["file"].raw = "new-user-path.%04d.exr"
            elif change == "proxy":
                proxy.raw = "true"
            elif change == "frame":
                nuke.frame = lambda: 2
            elif change == "topology":
                write.inputs = lambda: 1
                write.input = lambda _index: read
            else:
                nuke._plugin_folders.append("new-user-plugin")
        return result

    monkeypatch.setattr(script_package_scene, "on_host_thread", dispatch)
    with pytest.raises(FileReferenceRuntimeError) as error:
        script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used")

    assert calls == [1]
    assert error.value.context["reason"] == "scene_changed_since_snapshot"
    assert not nuke.saves


@pytest.mark.parametrize("stage", ["worker", "queued", "native_return"])
def test_real_request_cancellation_survives_a_fresh_main_thread_context(tmp_path, stage):
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    token = CancelToken()
    calls, dispatches = [], []
    write = Node("Write1", {}, "Write")

    def query(first, last):
        calls.append((first, last))
        if stage == "native_return":
            token.cancel()
        return []

    write.fileDependencies = query
    nuke.nodes.append(write)
    with ThreadPoolExecutor(max_workers=1) as host, ThreadPoolExecutor(max_workers=1) as worker:

        def dispatch(callback):
            dispatches.append(callback)
            if stage == "queued" and len(dispatches) == 2:
                token.cancel()
            return host.submit(callback).result(timeout=3)

        def run():
            reset = set_cancel_token(token)
            try:
                if stage == "worker":
                    token.cancel()
                return script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used")
            finally:
                reset_cancel_token(reset)

        nuke.executeInMainThreadWithResult = dispatch
        with pytest.raises(DccMcpCancelledError):
            worker.submit(run).result(timeout=5)
    assert calls == ([(1, 1)] if stage == "native_return" else [])
    assert not nuke.saves and not (tmp_path / "package").exists()


def test_query_budget_is_checked_before_any_dependency_query(tmp_path, monkeypatch):
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    calls = []
    for name in ("WriteA", "WriteB"):
        node = Node(name, {}, "Write")
        node.fileDependencies = lambda *_args: calls.append(True) or []
        nuke.nodes.append(node)
    monkeypatch.setattr(script_package_scene, "_MAX_DEPENDENCY_QUERIES", 3)
    monkeypatch.setattr(script_package, "sequence_files", lambda *_a, **_kw: pytest.fail("limited proof was scanned"))

    with pytest.raises(FileReferenceRuntimeError) as error:
        script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used")

    assert error.value.context["reason"] == "dependency_query_limit"
    assert error.value.context["query_count"] == 4
    assert not calls


@pytest.mark.parametrize("limit", ["rows", "paths"])
def test_total_native_return_budget_includes_repeated_rows_and_paths(tmp_path, monkeypatch, limit):
    frame = _source(tmp_path, "plate.0001.exr")
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    read = nuke.nodes[0]
    for name in ("WriteA", "WriteB"):
        nuke.nodes.append(Node(name, {}, "Write", dependencies=[(read, [str(frame), str(frame)])]))
    monkeypatch.setattr(script_package_scene, "_MAX_DEPENDENCY_" + limit.upper(), 3)

    with pytest.raises(FileReferenceRuntimeError) as error:
        script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used", max_files=1)

    assert error.value.context["reason"] == "dependency_query_limit"
    assert error.value.context["limit"] == limit
    assert not nuke.saves


def test_signature_work_is_separate_from_query_count_and_each_query_is_bounded(tmp_path, monkeypatch):
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    nuke.nodes.extend(Node(f"NoOp{index}", {"label": Knob("", "String_Knob")}, "NoOp") for index in range(218))
    nuke.nodes.append(Node("Write1", {}, "Write"))
    original = script_package_scene.scene_signature
    checks = []

    def signature(host, max_nodes):
        checks.append(len(host.nodes))
        return original(host, max_nodes)

    monkeypatch.setattr(script_package_scene, "scene_signature", signature)
    result = script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used")

    assert result["unused_references_count"] == 1
    # One initial snapshot, two guards per indivisible query, one final guard.
    # This records work for the 220-node fixture, without a machine-time claim.
    assert checks == [220] * 6


def test_old_receipts_without_unused_references_remain_readable(tmp_path):
    nuke = _nuke(tmp_path, _source(tmp_path))
    target = tmp_path / "package"
    script_package.package_script(nuke, str(target), dry_run=False)
    receipt = target / "manifest.json"
    manifest = json.loads(receipt.read_text(encoding="utf-8"))
    del manifest["unused_references"]
    receipt.write_text(json.dumps(manifest), encoding="utf-8")

    result = script_package.inspect_package(str(target))

    assert result["verified"] is True
    assert result["unused_references"] == []
    assert result["unused_references_count"] == 0


@pytest.mark.parametrize("key", ["files", "missing", "unrewritten", "runtime_dependencies"])
def test_receipts_still_require_original_report_fields(tmp_path, key):
    nuke = _nuke(tmp_path, _source(tmp_path))
    target = tmp_path / "package"
    script_package.package_script(nuke, str(target), dry_run=False)
    receipt = target / "manifest.json"
    manifest = json.loads(receipt.read_text(encoding="utf-8"))
    del manifest[key]
    receipt.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(FileReferenceError):
        script_package.inspect_package(str(target))
