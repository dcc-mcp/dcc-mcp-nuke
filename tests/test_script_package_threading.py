from __future__ import annotations

import importlib.util
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from test_script_package import SKILL_ROOT, Node, _nuke, _source

from dcc_mcp_nuke import script_package
from dcc_mcp_nuke.file_references import FileReferenceRuntimeError


def _route():
    spec = importlib.util.spec_from_file_location("collect_threading_route", SKILL_ROOT / "scripts/package_script.py")
    route = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(route)
    return route


@pytest.mark.parametrize("dry_run", [True, False])
def test_slow_filesystem_runs_on_worker_and_all_host_calls_use_native_main_bridge(tmp_path, monkeypatch, dry_run):
    nuke = _nuke(tmp_path, _source(tmp_path))
    entered, release = threading.Event(), threading.Event()
    sdk_threads, filesystem_threads = [], []
    with ThreadPoolExecutor(max_workers=1) as host, ThreadPoolExecutor(max_workers=1) as worker:
        host_id = host.submit(threading.get_ident).result()
        nuke.GUI = True
        nuke.executeInMainThreadWithResult = lambda callback: host.submit(callback).result(timeout=3)

        def observe(method):
            def call(*args, **kwargs):
                sdk_threads.append(threading.get_ident())
                return method(*args, **kwargs)

            return call

        for obj in (nuke, nuke._root, *nuke.nodes, nuke.Undo, *nuke.nodes[0].knobs().values()):
            for name in dir(obj):
                if not name.startswith("_") and name != "executeInMainThreadWithResult":
                    method = getattr(obj, name)
                    if callable(method):
                        monkeypatch.setattr(obj, name, observe(method))

        original_sequence = script_package.sequence_files
        original_stat = Path.stat

        def stat(path, *args, **kwargs):
            filesystem_threads.append(threading.get_ident())
            assert threading.get_ident() != host_id, "filesystem stat ran on the host main thread"
            return original_stat(path, *args, **kwargs)

        def slow_sequence(*args, **kwargs):
            filesystem_threads.append(threading.get_ident())
            entered.set()
            assert release.wait(3)
            return original_sequence(*args, **kwargs)

        monkeypatch.setattr(Path, "stat", stat)
        monkeypatch.setattr(script_package, "sequence_files", slow_sequence)
        future = worker.submit(script_package.package_script, nuke, str(tmp_path / "package"), dry_run=dry_run)
        try:
            assert entered.wait(2), "package never reached its worker filesystem phase"
            assert host.submit(lambda: 42).result(timeout=0.5) == 42
        finally:
            release.set()
        assert future.result(timeout=3)["dry_run"] is dry_run
        assert sdk_threads and set(sdk_threads) == {host_id}
        assert filesystem_threads and host_id not in filesystem_threads


@pytest.mark.parametrize("change", ["file", "root_name", "frames", "current_frame", "graph", "inputs", "plugin_paths"])
def test_publish_refuses_scene_changes_during_worker_scan_without_touching_new_user_state(
    tmp_path, monkeypatch, change
):
    original = _source(tmp_path)
    replacement = _source(tmp_path, "replacement.exr")
    nuke = _nuke(tmp_path, original)
    if change == "inputs":
        nuke.nodes[0].inputs = lambda: 1
        nuke.nodes[0].input = lambda _index: None
    target = tmp_path / "package"
    sequence = script_package.sequence_files

    def changed(*args, **kwargs):
        result = sequence(*args, **kwargs)
        if change == "file":
            nuke.nodes[0].knobs()["file"].raw = str(replacement)
        elif change == "root_name":
            nuke.root().setName(str(tmp_path / "new-user-script.nk"))
        elif change == "frames":
            nuke.root().lastFrame = lambda: 3
        elif change == "current_frame":
            nuke.frame = lambda: 2
        elif change == "inputs":
            nuke.nodes[0].input = lambda _index: nuke.nodes[0]
        elif change == "plugin_paths":
            nuke._plugin_folders.append(str(tmp_path / "user-plugin-path"))
        else:
            nuke.nodes.append(Node("NewUserNode", {}, "NoOp"))
        return result

    monkeypatch.setattr(script_package, "sequence_files", changed)
    with pytest.raises(FileReferenceRuntimeError, match="changed") as error:
        script_package.package_script(nuke, str(target), dry_run=False)
    assert error.value.context["reason"] == "scene_changed_since_snapshot"
    assert not nuke.saves and not target.exists()
    assert nuke.nodes[0].knobs()["file"].raw == str(replacement if change == "file" else original)
    assert nuke.root().modified() is True
    assert original.read_bytes() == b"pixels"


@pytest.mark.parametrize("proof", ["absent", "empty", "query_failed", "unreadable"])
def test_used_sequence_refusal_reports_offending_reference_and_bounded_reason(tmp_path, monkeypatch, proof):
    _source(tmp_path, "plate.0001.exr")
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    read = nuke.nodes[0]
    write = Node("Write1", {}, "Write", dependencies=[(read, [])] if proof == "empty" else [])
    if proof == "query_failed":
        write.fileDependencies = lambda *_args: (_ for _ in ()).throw(OSError("PRIVATE_DEPENDENCY_FAILURE"))
    if proof == "unreadable":
        read.knobs()["file"].evaluate = lambda: (_ for _ in ()).throw(OSError("PRIVATE_EVALUATION_FAILURE"))
    nuke.nodes.append(write)
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    monkeypatch.setattr(
        script_package, "sequence_files", lambda *_args, **_kwargs: pytest.fail("unproven sequence was scanned")
    )

    result = _route().main.__wrapped__(output_path=str(tmp_path / "package"), frame_mode="used")

    assert result["success"] is False
    context = result["context"]
    assert context["node"] == "Read0"
    assert context["knob"] == "file"
    assert Path(context["path"]) == tmp_path / "plate.%04d.exr"
    assert context["frame_range"] == [1, 2]
    assert (
        context["reason"]
        == {
            "absent": "dependency_not_reported",
            "empty": "dependency_paths_empty",
            "query_failed": "dependency_query_failed",
            "unreadable": "reference_unreadable",
        }[proof]
    )
    assert "PRIVATE_DEPENDENCY_FAILURE" not in str(result)
    assert "PRIVATE_EVALUATION_FAILURE" not in str(result)
    assert not nuke.saves and not (tmp_path / "package").exists()


def test_used_mode_probes_only_proven_concrete_paths_without_directory_globbing(tmp_path, monkeypatch):
    frame = _source(tmp_path, "plate.0009.exr")
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    nuke.nodes.append(Node("Write1", {}, "Write", dependencies=[(nuke.nodes[0], [str(frame)])]))
    monkeypatch.setattr(
        script_package, "sequence_files", lambda *_args, **_kwargs: pytest.fail("used frames were globbed")
    )
    result = script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used")
    assert [Path(item["source"]) for item in result["files"]] == [frame]


@pytest.mark.parametrize("dependency", ["other/plate.0001.exr", "plate.%04d.exr"])
def test_used_mode_rejects_paths_that_do_not_prove_this_sequence(tmp_path, dependency):
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    nuke.nodes.append(Node("Write1", {}, "Write", dependencies=[(nuke.nodes[0], [str(tmp_path / dependency)])]))
    with pytest.raises(FileReferenceRuntimeError) as error:
        script_package.package_script(nuke, str(tmp_path / "package"), frame_mode="used")
    assert error.value.context["reason"] == "dependency_path_mismatch"


def test_native_cumulative_dependency_rows_count_unique_files_and_do_not_repeat_paths(tmp_path):
    from dcc_mcp_nuke.script_package_scene import _used_files

    frame = str(tmp_path / "plate.0101.exr")
    read = Node("Read1", {}, "Read")
    first = Node("Write1", {}, "Write", dependencies=[(read, [frame]), (read, [frame, frame])])
    second = Node("Write2", {}, "Write", dependencies=[(read, [frame, frame])])

    used, error = _used_files({"Read1": read, "Write1": first, "Write2": second}, 1, 2, 1)

    assert error is None
    assert used == {"Read1": (frame,)}


def test_shared_file_preserves_every_native_source_node_proof(tmp_path):
    from dcc_mcp_nuke.script_package_scene import _used_files

    frame = str(tmp_path / "plate.0001.exr")
    reads = [Node("Read1", {}, "Read"), Node("Read2", {}, "Read")]
    write = Node("Write1", {}, "Write", dependencies=[(read, [frame, frame]) for read in reads])
    used, error = _used_files({"Write1": write}, 1, 2, 1)
    assert error is None
    assert used == {"Read1": (frame,), "Read2": (frame,)}


@pytest.mark.parametrize("invalid", [None, 1, "bad\x00path", "x" * 8193])
def test_repeated_valid_path_does_not_hide_invalid_native_dependency(tmp_path, invalid):
    from dcc_mcp_nuke.script_package_scene import _used_files

    frame = str(tmp_path / "plate.0001.exr")
    read = Node("Read1", {}, "Read")
    write = Node("Write1", {}, "Write", dependencies=[(read, [frame, frame]), (read, [frame, invalid])])
    _, error = _used_files({"Write1": write}, 1, 2, 1)
    assert error == {"query_node": "Write1", "reason": "dependency_query_failed"}
