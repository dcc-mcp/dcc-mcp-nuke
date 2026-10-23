"""Typed AIR workflows with additive graph changes and bounded native jobs."""

from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Any

from dcc_mcp_nuke.host_flavor import detect_host_flavor
from dcc_mcp_nuke.node_graph import _graph_snapshot, create_node_handle, delete_node_handle
from dcc_mcp_nuke.plugins import inspect_plugin_environment

AIR_CLASSES = ("CopyCat", "BigCat", "Inference", "Deblur", "Upscale", "CatFileCreator")
TRAINING_CLASSES = ("CopyCat", "BigCat")
X_CLASSES = (*TRAINING_CLASSES, "CatFileCreator")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_CHANNEL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")
_TRAINING_FIELDS = {
    "dataDirectory",
    "epochs",
    "initialWeights",
    "checkpointFile",
    "modelSize",
    "batchSizeType",
    "batchSize",
    "cropSize",
    "checkpointInterval",
    "imageInterval",
    "useMultiResolution",
}
_FIELDS = {
    "CopyCat": _TRAINING_FIELDS,
    "BigCat": _TRAINING_FIELDS | {"l1Weight", "l2Weight"},
    "Inference": {"modelFile", "halfPrecision", "tileSize"},
    "Deblur": {"halfPrecision", "tileSize", "mix"},
    "Upscale": {"halfPrecision", "tileSize"},
    "CatFileCreator": {"torchScriptFile", "catFile", "channelsIn", "channelsOut", "modelId", "outputScale"},
}
_PATH_FIELDS = {"dataDirectory", "checkpointFile", "modelFile", "torchScriptFile", "catFile"}
_ENUM_FIELDS = {"initialWeights", "modelSize", "batchSizeType", "cropSize", "tileSize"}
_BOOL_FIELDS = {"halfPrecision", "useMultiResolution"}
_INTEGER_FIELDS = {
    "epochs": (1, 1000000),
    "batchSize": (1, 256),
    "checkpointInterval": (1, 1000000),
    "imageInterval": (1, 1000000),
}


class MachineLearningError(ValueError):
    """An actionable AIR capability or operation failure."""

    def __init__(self, message: str, code: str = "invalid_request", **context: Any) -> None:
        super().__init__(message)
        self.code = code
        self.context = context


def _gate(nuke: Any, node_class: str) -> str:
    if node_class not in AIR_CLASSES:
        raise MachineLearningError("node_class must be one of the six AIR classes")
    version = str(getattr(nuke, "NUKE_VERSION_STRING", ""))
    match = re.match(r"^(\d+)\.(\d+)", version)
    minimum = {"BigCat": (16, 1), "CatFileCreator": (13, 1)}.get(node_class, (13, 0))
    if not match or tuple(map(int, match.groups())) < minimum:
        raise MachineLearningError(
            "AIR node is unavailable in this Nuke version",
            "capability_unavailable",
            node_class=node_class,
            minimum_version=list(minimum),
        )
    flavor = detect_host_flavor(nuke).flavor
    if node_class in X_CLASSES and flavor not in {"nukex", "nukestudio"}:
        raise MachineLearningError(
            "This AIR operation requires NukeX or Nuke Studio",
            "capability_unavailable",
            node_class=node_class,
            host_flavor=flavor,
            required_host_flavors=["nukex", "nukestudio"],
        )
    return flavor


def _path(value: Any, *, suffix: str | None = None, exists: bool = False) -> Path:
    if not isinstance(value, str) or not value or len(value) > 8192 or any(ord(c) < 32 or c in "[]$" for c in value):
        raise MachineLearningError("path must be an absolute local path")
    path = Path(value).expanduser()
    if not path.is_absolute() or str(value).startswith(("//", "\\\\")):
        raise MachineLearningError("path must be an absolute local path")
    if any(part in {".", ".."} for part in path.parts) or any(_is_link(item) for item in (path, *path.parents)):
        raise MachineLearningError("path must not traverse symlinks or relative components")
    if suffix and path.suffix.lower() != suffix:
        raise MachineLearningError(f"path must have the {suffix} suffix")
    if exists and (not path.is_file() or path.stat().st_size <= 0 or path.stat().st_size > 1024**3):
        raise MachineLearningError("model must be an existing nonempty file of at most 1 GiB")
    return path.resolve()


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except FileNotFoundError:
        return False


def _on_host_thread(nuke: Any, callback: Any) -> Any:
    if getattr(nuke, "GUI", False):
        dispatch = getattr(nuke, "executeInMainThreadWithResult", None)
        if not callable(dispatch):
            raise MachineLearningError("native main-thread dispatch is unavailable", "capability_unavailable")
        return dispatch(callback)
    return callback()


def _settings(node_class: str, settings: Any) -> dict[str, Any]:
    if settings is None:
        settings = {}
    if not isinstance(settings, dict) or set(settings) - _FIELDS[node_class]:
        raise MachineLearningError("settings contain an unsupported AIR knob")
    result = dict(settings)
    for name, value in result.items():
        if name in _PATH_FIELDS:
            suffix = (
                ".pt"
                if name == "torchScriptFile"
                else ".cat"
                if name in {"modelFile", "checkpointFile", "catFile"}
                else None
            )
            result[name] = _path(
                value, suffix=suffix, exists=name in {"torchScriptFile", "modelFile", "checkpointFile"}
            ).as_posix()
        elif name in _INTEGER_FIELDS:
            low, high = _INTEGER_FIELDS[name]
            if type(value) is not int or not low <= value <= high:
                raise MachineLearningError(f"{name} must be an integer from {low} through {high}")
        elif name in _BOOL_FIELDS:
            if type(value) is not bool:
                raise MachineLearningError(f"{name} must be boolean")
        elif name in _ENUM_FIELDS:
            if not isinstance(value, str) or not value or len(value) > 128:
                raise MachineLearningError(f"{name} must be an exact native enumeration value")
        elif name in {"channelsIn", "channelsOut"}:
            if (
                not isinstance(value, list)
                or not 1 <= len(value) <= 64
                or any(not isinstance(channel, str) or not _CHANNEL.fullmatch(channel) for channel in value)
                or len(set(value)) != len(value)
            ):
                raise MachineLearningError("channels must be a unique list of layer.channel names")
            result[name] = ",".join(value)
        elif name == "modelId":
            if not isinstance(value, str) or len(value) > 128 or any(ord(c) < 32 or c in "[]$" for c in value):
                raise MachineLearningError("modelId must be a bounded literal string")
        elif name == "outputScale":
            if (
                not isinstance(value, list)
                or len(value) != 2
                or any(type(v) not in (float, int) or not math.isfinite(v) or not 0 < v <= 16 for v in value)
            ):
                raise MachineLearningError("outputScale must contain two positive finite numbers no greater than 16")
        elif (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not 0 <= value <= (1 if name == "mix" else 100)
        ):
            raise MachineLearningError(f"invalid numeric {name}")
    return result


def _set(node: Any, name: str, value: Any) -> None:
    if name not in node.knobs():
        raise MachineLearningError(
            "required AIR knob is unavailable", "capability_unavailable", node_class=node.Class(), knob=name
        )
    knob = node[name]
    if name in _ENUM_FIELDS and value not in list(knob.values()):
        raise MachineLearningError(f"{name} must match a native enumeration value")
    knob.setValue(value)
    actual = [knob.value(i) for i in range(2)] if name == "outputScale" else knob.value()
    if actual != value:
        raise MachineLearningError("AIR knob readback differs from request", "host_error", knob=name)


def _selection(nuke: Any, previous: list[tuple[Any, bool]]) -> None:
    for node in nuke.allNodes(recurseGroups=True):
        node.setSelected(False)
    for node, selected in previous:
        node.setSelected(selected)


def _require_node(nuke: Any, name: str, classes: tuple[str, ...] = AIR_CLASSES) -> Any:
    if not isinstance(name, str) or not name or len(name) > 512:
        raise MachineLearningError("node_name must identify an existing AIR node")
    node = nuke.toNode(name)
    if node is None or node.fullName() != name or node.Class() not in classes:
        raise MachineLearningError("node_name must identify an existing supported AIR node")
    _gate(nuke, node.Class())
    return node


def create_ml_node(
    nuke: Any,
    node_class: str,
    *,
    name: str | None = None,
    input_node: str | None = None,
    reference_node: str | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create/configure one CPU AIR node, preserving outside graph and selection."""
    flavor = _gate(nuke, node_class)
    values = _settings(node_class, settings)
    if name is not None and (not isinstance(name, str) or not _IDENTIFIER.fullmatch(name)):
        raise MachineLearningError("name must be a simple node identifier of at most 64 characters")
    if node_class == "CatFileCreator" and (input_node or reference_node):
        raise MachineLearningError("CatFileCreator has no image inputs")
    if reference_node and node_class not in {*TRAINING_CLASSES, "Deblur"}:
        raise MachineLearningError("reference_node is supported only for training ground truth or a Deblur mask")
    sources = []
    for value in (input_node, reference_node):
        source = nuke.toNode(value) if isinstance(value, str) and value else None
        if value is not None and (source is None or source.fullName() != value):
            raise MachineLearningError("an explicit input node was not found")
        sources.append(source)
    root = nuke.root()
    with root:
        before = _graph_snapshot(nuke)
        previous = [(node, node.isSelected()) for node in before.values()]
        modified = root.modified()
        if type(modified) is not bool:
            raise MachineLearningError("Root modified state is unavailable", "host_error")
        prefix = name or node_class
        actual_name = prefix
        for index in range(1001):
            if nuke.toNode(actual_name) is None:
                break
            actual_name = f"{prefix}{index + 1}"
        else:
            raise MachineLearningError("node name collision limit exceeded")
        created = None
        try:
            for node, _selected in previous:
                node.setSelected(False)
            created = create_node_handle(nuke, node_class, name=actual_name)
            if node_class != "CatFileCreator":
                _set(created, "useGPUIfAvailable", False)
            for knob, value in values.items():
                _set(created, knob, value)
            for index, source in enumerate(sources):
                created.setInput(index, source) if source is not None else None
                if source is not None and created.input(index) != source:
                    raise MachineLearningError("AIR input readback differs from request", "host_error")
            after = _graph_snapshot(nuke)
            if any(after.get(key) != handle for key, handle in before.items()) or len(after) != len(before) + 1:
                raise MachineLearningError("AIR creation changed graph outside its requested node", "host_error")
            _selection(nuke, previous)
            return {
                "node": created.fullName(),
                "node_class": node_class,
                "host_flavor": flavor,
                "settings": values,
                "inputs": [source.fullName() if source is not None else None for source in sources],
                "device": "cpu",
                "execution_started": False,
            }
        except BaseException:
            try:
                if created is not None:
                    delete_node_handle(nuke, created)
                _selection(nuke, previous)
                root.setModified(modified)
                if _graph_snapshot(nuke) != before or root.modified() != modified:
                    raise RuntimeError
            except Exception:
                raise MachineLearningError(
                    "AIR creation failed and rollback could not be verified", "rollback_failed"
                ) from None
            raise


def inspect_ml_environment(
    nuke: Any,
    *,
    model_directories: list[str] | None = None,
    node_name: str | None = None,
    max_items: int = 128,
    max_scan_entries: int = 4096,
) -> dict[str, Any]:
    """Report readiness without creating a node or loading/deserializing models."""
    if (
        type(max_items) is not int
        or not 1 <= max_items <= 256
        or type(max_scan_entries) is not int
        or not 1 <= max_scan_entries <= 20000
    ):
        raise MachineLearningError("inspection limits are out of range")
    directories = [] if model_directories is None else model_directories
    if not isinstance(directories, list) or len(directories) > 8:
        raise MachineLearningError("model_directories must contain at most eight directories")
    roots = list(dict.fromkeys(_path(value) for value in directories))
    if any(not path.is_dir() for path in roots):
        raise MachineLearningError("model_directories must exist")
    inventory = inspect_plugin_environment(
        nuke, required_node_classes=list(AIR_CLASSES), max_items=max_items, max_scan_entries=max_scan_entries
    )
    assets = []
    scanned = 0
    incomplete = False
    stack = [(root, 0) for root in roots]
    while stack and scanned < max_scan_entries:
        directory, depth = stack.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if scanned >= max_scan_entries:
                        incomplete = True
                        break
                    scanned += 1
                    if (
                        entry.is_symlink()
                        or getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400
                    ):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if depth < 4:
                            stack.append((Path(entry.path), depth + 1))
                        else:
                            incomplete = True
                    elif Path(entry.name).suffix.lower() in {".cat", ".pt"}:
                        stat = entry.stat(follow_symlinks=False)
                        assets.append(
                            {
                                "path": str(Path(entry.path)),
                                "bytes": stat.st_size,
                                "suffix": Path(entry.name).suffix.lower(),
                                "runtime_validation": "not_run",
                            }
                        )
        except OSError:
            incomplete = True
    capabilities = []
    for item in inventory["requirements"]:
        try:
            _gate(nuke, item["node_class"])
            flavor_allowed = True
        except MachineLearningError:
            flavor_allowed = False
        capabilities.append(
            {**item, "host_version_and_flavor_allowed": flavor_allowed, "beta": item["node_class"] == "BigCat"}
        )
    result = {
        "host": inventory["host"],
        "host_flavor": detect_host_flavor(nuke).flavor,
        "nodes": capabilities,
        "model_assets": assets[:max_items],
        "model_asset_count": len(assets),
        "models_truncated": len(assets) > max_items,
        "scan_incomplete": incomplete or bool(stack),
        "scanned_entries": scanned,
        "model_loading_performed": False,
        "scope": "read-only metadata; candidates and files do not prove model execution or license availability",
    }
    if node_name is not None:
        node = _require_node(nuke, node_name)
        result["node"] = {
            "name": node.fullName(),
            "node_class": node.Class(),
            "knobs": {name: node[name].Class() for name in sorted(_FIELDS[node.Class()]) if name in node.knobs()},
        }
    return result


def train_ml_node(nuke: Any, node_name: str, output_directory: str, *, max_runtime_seconds: int = 60) -> dict[str, Any]:
    """Run an existing CPU trainer in an owned, time-limited NukeX process."""
    from dcc_mcp_nuke.ml_job import run_ml_job

    def preflight():
        node = _require_node(nuke, node_name, TRAINING_CLASSES)
        if node.input(0) is None or node.input(1) is None:
            raise MachineLearningError("training requires input and ground-truth nodes")
        epochs = node["epochs"].value()
        if (
            type(epochs) not in (int, float)
            or not math.isfinite(epochs)
            or not 1 <= epochs <= 1000
            or int(epochs) != epochs
        ):
            raise MachineLearningError("bounded training requires integral epochs from 1 through 1000")
        if node["useGPUIfAvailable"].value() is not False:
            raise MachineLearningError("bounded training supports an explicitly CPU-configured node")
        return node

    node = _on_host_thread(nuke, preflight)
    return run_ml_job(
        nuke, node, "train", _path(output_directory), max_runtime_seconds=max_runtime_seconds, preflight=preflight
    )


def convert_torchscript_model(
    nuke: Any, node_name: str, output_path: str, *, max_runtime_seconds: int = 60
) -> dict[str, Any]:
    """Convert a caller-selected trusted TorchScript model in an owned process."""
    from dcc_mcp_nuke.ml_job import run_ml_job

    def preflight():
        node = _require_node(nuke, node_name, ("CatFileCreator",))
        _path(node["torchScriptFile"].getText(), suffix=".pt", exists=True)
        for name in ("channelsIn", "channelsOut"):
            value = node[name].getText()
            _settings("CatFileCreator", {name: value.split(",")})
        model_id = node["modelId"].getText()
        _settings("CatFileCreator", {"modelId": model_id})
        if not model_id.strip():
            raise MachineLearningError("native CatFileCreator conversion requires a nonempty modelId")
        return node

    node = _on_host_thread(nuke, preflight)
    return run_ml_job(
        nuke,
        node,
        "convert",
        _path(output_path, suffix=".cat"),
        max_runtime_seconds=max_runtime_seconds,
        preflight=preflight,
    )
