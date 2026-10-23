"""Native-only scene snapshots for the collection worker; no filesystem probes."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from dcc_mcp_nuke.file_references import (
    FileReferenceRuntimeError,
    FileReferenceValidationError,
    _bounded_int,
    _describe,
    _references,
)

OUTPUT_CLASSES = frozenset({"Write", "DeepWrite", "WriteGeo", "WriteGeo2"})


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
    used_error: dict[str, str] | None
    signature: tuple[Any, ...] | None


def on_host_thread(nuke: Any, callback: Callable[[], Any]) -> Any:
    """Use Nuke's SDK bridge rather than moving host handles to a worker."""
    if threading.current_thread() is threading.main_thread():
        return callback()
    dispatch = getattr(nuke, "executeInMainThreadWithResult", None)
    if not callable(dispatch):
        raise PackageScriptError("Nuke main-thread snapshot dispatch is unavailable", "snapshot_dispatch_unavailable")
    return dispatch(callback)


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
    used, used_error = _used_files(nodes, first, last, max_files) if frame_mode == "used" else ({}, None)
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
        used=used,
        used_error=used_error,
        signature=scene_signature(nuke, max_references) if require_signature else None,
    )


def _used_files(nodes: dict[str, Any], first: int, last: int, max_files: int):
    queries = [node for node in nodes.values() if node.Class() in OUTPUT_CLASSES] or list(nodes.values())
    used: dict[str, tuple[str, ...]] = {}
    count = 0
    for node in queries:
        try:
            dependencies = node.fileDependencies(first, last)
            for dependency, paths in dependencies:
                if not isinstance(paths, (list, tuple)) or any(
                    not isinstance(path, str) or len(path) > 8192 or "\x00" in path for path in paths
                ):
                    raise ValueError
                count += len(paths)
                if count > max_files:
                    raise ValueError
                name = dependency.fullName()
                used[name] = (*used.get(name, ()), *paths)
        except Exception:
            return used, {"query_node": str(node.fullName())[:256], "reason": "dependency_query_failed"}
    return used, None
