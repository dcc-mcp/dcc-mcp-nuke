"""Native-only scene snapshots for the collection worker; no filesystem probes."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, Callable

from dcc_mcp_core.cancellation import DccMcpCancelledError, check_cancelled, current_cancel_token

from dcc_mcp_nuke.file_references import (
    FileReferenceRuntimeError,
    FileReferenceValidationError,
    _bounded_int,
    _describe,
    _references,
)

OUTPUT_CLASSES = frozenset({"Write", "DeepWrite", "WriteGeo", "WriteGeo2"})
_MAX_DEPENDENCY_QUERIES = 100000
_MAX_DEPENDENCY_ROWS = 100000
_MAX_DEPENDENCY_PATHS = 1000000


class PackageScriptError(FileReferenceRuntimeError):
    """An actionable, bounded collection refusal with no raw host exception."""

    def __init__(self, message: str, reason: str, **context: Any) -> None:
        super().__init__(message)
        self.context = dict(context, reason=reason)


@dataclass(frozen=True)
class PackageScene:
    original_script: str
    project_directory: str
    first: int
    last: int
    records: tuple[dict[str, Any], ...]
    fonts: tuple[dict[str, Any], ...]
    node_plugins: tuple[dict[str, Any], ...]
    plugins: tuple[str, ...]
    plugin_paths: tuple[str, ...]
    used: dict[str, tuple[str, ...]]
    query_nodes: tuple[tuple[str, str], ...]
    has_outputs: bool
    used_complete: bool
    signature: tuple[Any, ...] | None


def on_host_thread(nuke: Any, callback: Callable[[], Any]) -> Any:
    """Use Nuke's SDK bridge rather than moving host handles to a worker."""
    probe = current_cancel_token()

    def run():
        _check_probe(probe)
        return callback()

    if threading.current_thread() is threading.main_thread():
        return run()
    dispatch = getattr(nuke, "executeInMainThreadWithResult", None)
    if not callable(dispatch):
        raise PackageScriptError("Nuke main-thread snapshot dispatch is unavailable", "snapshot_dispatch_unavailable")
    return dispatch(run)


def _check_probe(probe):
    # Core 0.20.14 tokens expose cancelled but do not yet provide check().
    if probe is not None and probe.cancelled:
        raise DccMcpCancelledError("Request cancelled by client")


def serialization(knob: Any, context: Any) -> str:
    value = knob.toScript(False, context)
    if not isinstance(value, str) or len(value) > 1000000:
        raise FileReferenceRuntimeError("knob full-context serialization is not a bounded string")
    return value


def scene_signature(nuke: Any, max_nodes: int) -> tuple[Any, ...]:
    """Prove graph, dependencies and Root state before temporary publication edits."""
    root = nuke.root()
    nodes = [root, *sorted(nuke.allNodes(group=root, recurseGroups=True), key=lambda node: node.fullName())]
    if len(nodes) - 1 > max_nodes:
        raise PackageScriptError("Nuke scene changed since collection snapshot", "scene_changed_since_snapshot")
    result = [
        (root.name(), root.modified(), root.firstFrame(), root.lastFrame(), nuke.frame(), tuple(nuke.pluginPath()))
    ]
    size = 0
    for node in nodes:
        knobs = []
        for name, knob in sorted(node.knobs().items()):
            value = serialization(knob, None)
            size += len(value)
            if size > 50000000:
                raise PackageScriptError("scene serialization exceeds snapshot limit", "scene_snapshot_limit")
            knobs.append((name, value))
        count = node.inputs() if node is not root else 0
        if type(count) is not int or not 0 <= count <= max_nodes:
            raise PackageScriptError("scene inputs cannot be snapshotted", "scene_snapshot_unavailable")
        inputs = tuple(
            node.input(index).fullName() if node.input(index) is not None else None for index in range(count)
        )
        result.append((node.fullName(), node.Class(), tuple(knobs), inputs))
    return tuple(result)


def capture_scene(
    nuke: Any,
    *,
    frame_mode: str,
    first_frame: int | None,
    last_frame: int | None,
    max_references: int,
    max_files: int,
    require_signature: bool,
) -> PackageScene:
    root = nuke.root()
    first = root.firstFrame() if first_frame is None else first_frame
    last = root.lastFrame() if last_frame is None else last_frame
    _bounded_int("first_frame", first, -1000000, 1000000)
    _bounded_int("last_frame", last, -1000000, 1000000)
    if last < first or last - first > 100000:
        raise FileReferenceValidationError("frame range must be ordered and span at most 100001 frames")
    references, count = _references(nuke, max_references, None, include_exists=False)
    nodes = {node.fullName(): node for node in nuke.allNodes(group=root, recurseGroups=True)}
    if count > max_references or len(nodes) > max_references:
        raise FileReferenceValidationError("references or node count exceeds max_references")
    for name, knob in sorted(root.knobs().items()):
        if name != "name" and knob.Class() == "File_Knob":
            references.append((_describe(root, "Root", name, knob, include_exists=False), knob))
    if len(references) > max_references:
        raise FileReferenceValidationError("references including Root exceed max_references")
    records = []
    for record, knob in references:
        records.append(
            dict(record, multiple_contexts=serialization(knob, None) != serialization(knob, nuke.OutputContext()))
        )
    fonts, node_plugins = [], []
    for name, node in nodes.items():
        try:
            filename = node.filename() if callable(getattr(node, "filename", None)) else None
        except Exception:
            filename = None
        if not isinstance(filename, str) or len(filename) > 8192:
            filename = None
        node_plugins.append({"node": name, "class": str(node.Class()), "filename": filename})
        for knob_name, knob in node.knobs().items():
            if knob_name == "font" and knob.Class() in ("FreeType_Knob", "Font_Knob"):
                fonts.append(
                    {
                        "node": name,
                        "class": str(node.Class()),
                        "knob": knob_name,
                        "serialized": serialization(knob, nuke.OutputContext()),
                        "full_serialized": serialization(knob, None),
                        "static": knob.isAnimated() is False and knob.hasExpression() is False,
                    }
                )
    try:
        plugins = nuke.plugins(nuke.ALL | nuke.REGISTERED)
        if not isinstance(plugins, list) or len(plugins) > max_files:
            plugins = []
    except Exception:
        plugins = []
    try:
        folders = nuke.pluginPath()
        if not isinstance(folders, list) or len(folders) > 1024:
            folders = []
    except Exception:
        folders = []
    try:
        project = root["project_directory"].evaluate()
    except Exception:
        project = ""
    outputs = tuple(sorted((name, node.Class()) for name, node in nodes.items() if node.Class() in OUTPUT_CLASSES))
    return PackageScene(
        original_script=str(root.name()),
        project_directory=project if isinstance(project, str) else "",
        first=first,
        last=last,
        records=tuple(records),
        fonts=tuple(fonts),
        node_plugins=tuple(node_plugins),
        plugins=tuple(value for value in plugins if isinstance(value, str) and len(value) <= 8192),
        plugin_paths=tuple(value for value in folders if isinstance(value, str) and len(value) <= 8192),
        used={},
        query_nodes=outputs or tuple(sorted((name, node.Class()) for name, node in nodes.items())),
        has_outputs=bool(outputs),
        used_complete=False,
        signature=scene_signature(nuke, max_references) if require_signature or frame_mode == "used" else None,
    )


def capture_used_files(nuke: Any, scene: PackageScene, max_files: int, max_references: int) -> PackageScene:
    """Yield the host between single-frame queries; only plain records reach this worker."""
    check_cancelled()
    query_count = len(scene.query_nodes) * (scene.last - scene.first + 1)
    if query_count > _MAX_DEPENDENCY_QUERIES:
        raise PackageScriptError(
            "used-frame collection exceeds the native query budget; reduce the frame range",
            "dependency_query_limit",
            frame_range=[scene.first, scene.last],
            query_count=query_count,
            max_queries=_MAX_DEPENDENCY_QUERIES,
        )
    probe = current_cancel_token()
    used: dict[str, dict[str, None]] = {}
    unique_paths: set[str] = set()
    row_count = path_count = 0
    for query_name, query_class in scene.query_nodes:
        for frame in range(scene.first, scene.last + 1):
            check_cancelled()
            try:
                rows = on_host_thread(
                    nuke,
                    partial(
                        _query_used_frame,
                        nuke,
                        scene,
                        query_name,
                        query_class,
                        frame,
                        max_references,
                        probe,
                        _MAX_DEPENDENCY_ROWS - row_count,
                        _MAX_DEPENDENCY_PATHS - path_count,
                    ),
                )
            except (PackageScriptError, DccMcpCancelledError):
                raise
            except Exception:
                raise _dependency_failure(scene, query_name, query_class, frame) from None
            check_cancelled()
            row_count += len(rows)
            for name, paths in rows:
                check_cancelled()
                path_count += len(paths)
                files = used.setdefault(name, {})
                for path in paths:
                    check_cancelled()
                    unique_paths.add(path)
                    if len(unique_paths) > max_files:
                        raise _dependency_failure(scene, query_name, query_class, frame)
                    files[path] = None
    check_cancelled()
    on_host_thread(nuke, lambda: _assert_scene_unchanged(nuke, scene, max_references))
    return replace(scene, used={name: tuple(files) for name, files in used.items()}, used_complete=True)


def _assert_scene_unchanged(nuke, scene, max_references):
    if scene.signature is None or scene_signature(nuke, max_references) != scene.signature:
        raise PackageScriptError("Nuke scene changed since collection snapshot", "scene_changed_since_snapshot")


def _query_used_frame(nuke, scene, query_name, query_class, frame, max_references, probe, rows_left, paths_left):
    # One indivisible SDK call can still block. Never pump nested Qt events or
    # move a Nuke handle to the worker to hide that native limitation.
    _check_probe(probe)
    _assert_scene_unchanged(nuke, scene, max_references)
    nodes = {node.fullName(): node for node in nuke.allNodes(group=nuke.root(), recurseGroups=True)}
    dependencies = nodes[query_name].fileDependencies(frame, frame)
    _check_probe(probe)
    if not isinstance(dependencies, (list, tuple)):
        raise ValueError
    if len(dependencies) > rows_left:
        raise _dependency_failure(scene, query_name, query_class, frame, "dependency_query_limit", limit="rows")
    rows = []
    for row in dependencies:
        _check_probe(probe)
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ValueError
        dependency, paths = row
        if not isinstance(paths, (list, tuple)):
            raise ValueError
        paths_left -= len(paths)
        if paths_left < 0:
            raise _dependency_failure(scene, query_name, query_class, frame, "dependency_query_limit", limit="paths")
        name = dependency.fullName()
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 256
            or "\x00" in name
            or name not in nodes
            or any(not isinstance(path, str) or len(path) > 8192 or "\x00" in path for path in paths)
        ):
            raise ValueError
        rows.append((name, tuple(paths)))
    _assert_scene_unchanged(nuke, scene, max_references)
    return tuple(rows)


def _dependency_failure(scene, query_name, query_class, frame, reason="dependency_query_failed", **extra):
    reference = next((item for item in scene.records if item["class"] not in OUTPUT_CLASSES | {"Root"}), None)
    identity = (
        {key: reference[key] for key in ("node", "class", "knob")}
        if reference
        else {"node": query_name[:256], "class": query_class[:128], "knob": "file"}
    )
    return PackageScriptError(
        "used-frame dependencies could not be proven for a reference",
        reason,
        **identity,
        path=((reference["raw_value"] or reference["evaluated_path"] or "") if reference else "")[:8192],
        frame_range=[scene.first, scene.last],
        query_node=query_name[:256],
        query_frame=frame,
        **extra,
    )
