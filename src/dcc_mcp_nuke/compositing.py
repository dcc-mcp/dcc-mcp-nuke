from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

MERGE_OPERATIONS = {"over", "plus", "multiply", "screen", "max", "min"}


def validate_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a deterministic layered-composite manifest."""
    if not isinstance(manifest, Mapping):
        raise ValueError("manifest must be an object")

    normalized = dict(manifest)
    for key in ("script_path", "output_path"):
        value = normalized.get(key)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError(f"{key} must be an absolute path")

    layers = normalized.get("layers")
    if not isinstance(layers, list) or not layers:
        raise ValueError("layers must contain at least one layer")

    normalized_layers = []
    for index, layer in enumerate(layers):
        if not isinstance(layer, Mapping):
            raise ValueError(f"layers[{index}] must be an object")
        path = layer.get("path")
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise ValueError(f"layers[{index}].path must be an absolute path")
        operation = str(layer.get("operation", "over")).lower()
        if operation not in MERGE_OPERATIONS:
            raise ValueError(f"layers[{index}].operation must be one of {sorted(MERGE_OPERATIONS)}")
        normalized_layers.append(
            {
                "name": str(layer.get("name") or f"layer_{index + 1}"),
                "path": path,
                "operation": operation,
                "colorspace": layer.get("colorspace"),
            }
        )

    first = int(normalized.get("first_frame", 1))
    last = int(normalized.get("last_frame", first))
    if first < 0 or last < first:
        raise ValueError("frame range must satisfy 0 <= first_frame <= last_frame")

    script_path = Path(normalized["script_path"])
    if script_path.suffix.lower() != ".nk":
        raise ValueError("script_path must end in .nk")

    normalized.update(
        layers=normalized_layers,
        first_frame=first,
        last_frame=last,
        width=int(normalized.get("width", 1920)),
        height=int(normalized.get("height", 1080)),
        fps=float(normalized.get("fps", 30.0)),
    )
    if normalized["width"] <= 0 or normalized["height"] <= 0 or normalized["fps"] <= 0:
        raise ValueError("width, height, and fps must be positive")
    return normalized


def build_layered_comp(nuke: Any, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Replace the current graph with a layered Read/Merge/Write composite."""
    spec = validate_manifest(manifest)
    for node in list(nuke.allNodes(recurseGroups=False)):
        nuke.delete(node)

    root = nuke.root()
    root["first_frame"].setValue(spec["first_frame"])
    root["last_frame"].setValue(spec["last_frame"])
    root["fps"].setValue(spec["fps"])
    format_name = "dcc_mcp_output"
    nuke.addFormat(f"{spec['width']} {spec['height']} 1 {format_name}")
    root["format"].setValue(format_name)

    reads = []
    current = None
    for index, layer in enumerate(spec["layers"]):
        read = nuke.nodes.Read(file=_nuke_path(layer["path"]))
        _set_read_frame_range(read, spec["first_frame"], spec["last_frame"])
        read.setName(_node_name(layer["name"], f"Layer_{index + 1}"))
        if layer["colorspace"] and "colorspace" in read.knobs():
            read["colorspace"].setValue(str(layer["colorspace"]))
        reads.append(read.name())
        if current is None:
            current = read
            continue
        merge = nuke.nodes.Merge2()
        merge.setName(f"Merge_{index + 1:02d}_{layer['operation']}")
        merge["operation"].setValue(layer["operation"])
        _connect_merge(merge, background=current, foreground=read)
        current = merge

    output = Path(spec["output_path"])
    output.parent.mkdir(parents=True, exist_ok=True)
    write = nuke.nodes.Write(file=_nuke_path(output))
    write.setName("DCC_MCP_FINAL_WRITE")
    write.setInput(0, current)
    if "file_type" in write.knobs():
        write["file_type"].setValue(_file_type(output))

    script = Path(spec["script_path"])
    script.parent.mkdir(parents=True, exist_ok=True)
    _save_script(nuke, script)
    return {
        "script_path": str(script),
        "output_path": str(output),
        "write_node": write.name(),
        "read_nodes": reads,
        "first_frame": spec["first_frame"],
        "last_frame": spec["last_frame"],
    }


def _set_read_frame_range(read: Any, first: int, last: int) -> None:
    """Apply the manifest range to Read knobs created through the Nuke API."""
    knobs = read.knobs()
    for name, value in (("first", first), ("last", last), ("origfirst", first), ("origlast", last)):
        if name in knobs:
            read[name].setValue(value)


def _connect_merge(merge: Any, background: Any, foreground: Any) -> None:
    """Connect Nuke Merge B (background) and A (foreground) inputs explicitly."""
    merge.setInput(0, background)
    merge.setInput(1, foreground)


def _save_script(nuke: Any, path: str | Path) -> None:
    """Save without a GUI overwrite prompt so repeated builds stay idempotent."""
    nuke.scriptSaveAs(_nuke_path(path), overwrite=1)


def _node_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
    return cleaned or fallback


def _file_type(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    return "jpeg" if suffix in {"jpg", "jpeg"} else suffix


def _nuke_path(path: str | Path) -> str:
    """Use forward slashes so Nuke/Tcl does not consume Windows escapes."""
    return str(path).replace("\\", "/")
