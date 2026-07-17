from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

MERGE_OPERATIONS = {"over", "plus", "minus", "multiply", "screen", "max", "min"}
ADJUSTMENT_KINDS = {"grade", "material_gain", "blur"}
NODE_COLUMN_SPACING = 320
NODE_ROW_SPACING = 100
MATTE_BRANCH_OFFSET = 140


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
        channel = layer.get("channel")
        if channel is not None and (not isinstance(channel, str) or not channel.strip()):
            raise ValueError(f"layers[{index}].channel must be a non-empty string")
        normalized_layers.append(
            {
                "name": str(layer.get("name") or f"layer_{index + 1}"),
                "path": path,
                "operation": operation,
                "channel": channel,
                "colorspace": layer.get("colorspace"),
                "gain": float(layer.get("gain", 1.0)),
                "blur_size": float(layer.get("blur_size", 0.0)),
                "adjustments": _normalize_adjustments(layer, index),
            }
        )
        if normalized_layers[-1]["gain"] < 0:
            raise ValueError(f"layers[{index}].gain must be non-negative")
        if normalized_layers[-1]["blur_size"] < 0:
            raise ValueError(f"layers[{index}].blur_size must be non-negative")

    required_layers = normalized.get("required_layers", [])
    if not isinstance(required_layers, list) or any(
        not isinstance(layer, str) or not layer.strip() for layer in required_layers
    ):
        raise ValueError("required_layers must be an array of non-empty strings")
    for key in ("output_colorspace", "output_datatype"):
        value = normalized.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{key} must be a non-empty string")

    first = int(normalized.get("first_frame", 1))
    last = int(normalized.get("last_frame", first))
    if first < 0 or last < first:
        raise ValueError("frame range must satisfy 0 <= first_frame <= last_frame")

    script_path = Path(normalized["script_path"])
    if script_path.suffix.lower() != ".nk":
        raise ValueError("script_path must end in .nk")

    normalized.update(
        layers=normalized_layers,
        required_layers=list(dict.fromkeys(required_layers)),
        first_frame=first,
        last_frame=last,
        width=int(normalized.get("width", 1920)),
        height=int(normalized.get("height", 1080)),
        fps=float(normalized.get("fps", 30.0)),
        output_colorspace=normalized.get("output_colorspace"),
        output_datatype=normalized.get("output_datatype"),
    )
    if normalized["width"] <= 0 or normalized["height"] <= 0 or normalized["fps"] <= 0:
        raise ValueError("width, height, and fps must be positive")
    return normalized


def _normalize_adjustments(layer: Mapping[str, Any], layer_index: int) -> list[dict[str, Any]]:
    adjustments = layer.get("adjustments")
    if adjustments is None:
        return []
    if float(layer.get("gain", 1.0)) != 1.0 or float(layer.get("blur_size", 0.0)) != 0.0:
        raise ValueError(f"layers[{layer_index}] cannot mix adjustments with non-default gain or blur_size")
    if not isinstance(adjustments, list):
        raise ValueError(f"layers[{layer_index}].adjustments must be an array")

    normalized = []
    for index, adjustment in enumerate(adjustments):
        label = f"layers[{layer_index}].adjustments[{index}]"
        if not isinstance(adjustment, Mapping):
            raise ValueError(f"{label} must be an object")
        kind = str(adjustment.get("kind", "")).lower()
        if kind not in ADJUSTMENT_KINDS:
            raise ValueError(f"{label}.kind must be one of {sorted(ADJUSTMENT_KINDS)}")
        name = adjustment.get("name")
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise ValueError(f"{label}.name must be a non-empty string")

        if kind == "grade":
            gain = float(adjustment.get("gain", 1.0))
            gamma = float(adjustment.get("gamma", 1.0))
            if gain < 0 or gamma <= 0:
                raise ValueError(f"{label} requires gain >= 0 and gamma > 0")
            normalized.append({"kind": kind, "name": name, "gain": gain, "gamma": gamma})
        elif kind == "blur":
            size = float(adjustment.get("size", 0.0))
            if size < 0:
                raise ValueError(f"{label}.size must be non-negative")
            normalized.append({"kind": kind, "name": name, "size": size})
        else:
            crypto_layer = adjustment.get("crypto_layer")
            materials = adjustment.get("materials")
            gain = float(adjustment.get("gain", 1.0))
            if not isinstance(crypto_layer, str) or not crypto_layer.strip():
                raise ValueError(f"{label}.crypto_layer must be a non-empty string")
            if (
                not isinstance(materials, list)
                or not materials
                or any(not isinstance(material, str) or not material.strip() for material in materials)
            ):
                raise ValueError(f"{label}.materials must contain non-empty strings")
            if gain < 0:
                raise ValueError(f"{label}.gain must be non-negative")
            normalized.append(
                {
                    "kind": kind,
                    "name": name,
                    "crypto_layer": crypto_layer,
                    "materials": materials,
                    "gain": gain,
                }
            )
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
    channel_nodes = []
    adjustments = []
    current = None
    current_y = 0
    merge_start_y = (max(_layer_row_count(layer) for layer in spec["layers"]) + 1) * NODE_ROW_SPACING
    for index, layer in enumerate(spec["layers"]):
        layer_x = index * NODE_COLUMN_SPACING
        read = nuke.nodes.Read(file=_nuke_path(layer["path"]))
        _set_read_frame_range(read, spec["first_frame"], spec["last_frame"])
        read.setName(_node_name(layer["name"], f"Layer_{index + 1}"))
        _set_node_position(read, layer_x, 0)
        if layer["colorspace"] and "colorspace" in read.knobs():
            read["colorspace"].setValue(str(layer["colorspace"]))
        reads.append(read.name())
        requirements = [*spec["required_layers"]]
        if layer["channel"]:
            requirements.append(layer["channel"])
        requirements.extend(
            adjustment["crypto_layer"] for adjustment in layer["adjustments"] if adjustment["kind"] == "material_gain"
        )
        if requirements:
            _require_layers(read, list(dict.fromkeys(requirements)))

        source = read
        adjustment_y = NODE_ROW_SPACING
        if layer["channel"]:
            source = _shuffle_layer(nuke, read, layer["channel"], index)
            _set_node_position(source, layer_x, adjustment_y)
            adjustment_y += NODE_ROW_SPACING
            channel_nodes.append(source.name())
        adjusted, created = _apply_layer_adjustments(
            nuke,
            source,
            layer,
            index,
            matte_source=read,
            layout_origin=(layer_x, adjustment_y),
        )
        adjustments.extend(created)
        if current is None:
            current = adjusted
            current_y = _layer_row_count(layer) * NODE_ROW_SPACING
            continue
        merge = nuke.nodes.Merge2()
        merge.setName(f"Merge_{index + 1:02d}_{layer['operation']}")
        merge["operation"].setValue(layer["operation"])
        _connect_merge(merge, background=current, foreground=adjusted)
        current_y = merge_start_y + (index - 1) * NODE_ROW_SPACING
        _set_node_position(merge, layer_x, current_y)
        current = merge

    output = Path(spec["output_path"])
    output.parent.mkdir(parents=True, exist_ok=True)
    current, output_format_node = _apply_output_format(nuke, current, format_name)
    output_x = (len(spec["layers"]) - 1) * NODE_COLUMN_SPACING
    current_y += NODE_ROW_SPACING
    _set_node_position(current, output_x, current_y)
    write = nuke.nodes.Write(file=_nuke_path(output))
    write.setName("DCC_MCP_FINAL_WRITE")
    write.setInput(0, current)
    _set_node_position(write, output_x, current_y + NODE_ROW_SPACING)
    if "file_type" in write.knobs():
        write["file_type"].setValue(_file_type(output))
    _set_write_options(write, spec)

    script = Path(spec["script_path"])
    script.parent.mkdir(parents=True, exist_ok=True)
    _save_script(nuke, script)
    return {
        "script_path": str(script),
        "output_path": str(output),
        "write_node": write.name(),
        "read_nodes": reads,
        "channel_nodes": channel_nodes,
        "adjustment_nodes": adjustments,
        "output_format_node": output_format_node,
        "first_frame": spec["first_frame"],
        "last_frame": spec["last_frame"],
    }


def _apply_output_format(nuke: Any, node: Any, format_name: str) -> tuple[Any, str]:
    """Make the manifest resolution authoritative instead of inheriting the input format."""
    reformat = nuke.nodes.Reformat()
    reformat.setName("DCC_MCP_OUTPUT_FORMAT")
    reformat.setInput(0, node)
    knobs = reformat.knobs()
    if "type" in knobs:
        reformat["type"].setValue("to format")
    if "format" in knobs:
        reformat["format"].setValue(format_name)
    if "resize" in knobs:
        reformat["resize"].setValue("fit")
    return reformat, reformat.name()


def _shuffle_layer(nuke: Any, read: Any, channel: str, index: int) -> Any:
    shuffle = nuke.nodes.Shuffle()
    shuffle.setName(f"Shuffle_{index + 1:02d}_{_node_name(channel, 'layer')}")
    shuffle.setInput(0, read)
    input_knob = "in" if "in" in shuffle.knobs() else "in1"
    shuffle[input_knob].setValue(channel)
    return shuffle


def _apply_layer_adjustments(
    nuke: Any,
    node: Any,
    layer: Mapping[str, Any],
    index: int,
    *,
    matte_source: Any | None = None,
    layout_origin: tuple[int, int] | None = None,
) -> tuple[Any, list[str]]:
    """Apply legacy gain/blur or an ordered, bounded adjustment pipeline."""
    current = node
    created = []
    layout_x, layout_y = layout_origin or (0, 0)
    if layer.get("adjustments"):
        for operation_index, operation in enumerate(layer["adjustments"]):
            fallback = f"{operation['kind']}_{index + 1:02d}_{operation_index + 1:02d}"
            name = _node_name(operation.get("name") or fallback, fallback)
            if operation["kind"] == "grade":
                grade = nuke.nodes.Grade()
                grade.setName(name)
                grade.setInput(0, current)
                grade["multiply"].setValue(operation["gain"])
                grade["gamma"].setValue(operation["gamma"])
                if layout_origin is not None:
                    _set_node_position(grade, layout_x, layout_y)
                    layout_y += NODE_ROW_SPACING
                current = grade
                created.append(grade.name())
            elif operation["kind"] == "blur":
                blur = nuke.nodes.Blur()
                blur.setName(name)
                blur.setInput(0, current)
                blur["size"].setValue(operation["size"])
                if layout_origin is not None:
                    _set_node_position(blur, layout_x, layout_y)
                    layout_y += NODE_ROW_SPACING
                current = blur
                created.append(blur.name())
            else:
                matte = nuke.nodes.Cryptomatte()
                matte.setName(f"{name}_Crypto")
                matte.setInput(0, matte_source if matte_source is not None else node)
                matte["cryptoLayer"].setValue(operation["crypto_layer"])
                matte["matteList"].setValue(", ".join(operation["materials"]))

                attenuated = nuke.nodes.Grade()
                attenuated.setName(f"{name}_Attenuated")
                attenuated.setInput(0, current)
                attenuated["multiply"].setValue(operation["gain"])

                keymix = nuke.nodes.Keymix()
                keymix.setName(f"{name}_Material_Keymix")
                keymix.setInput(0, current)
                keymix.setInput(1, attenuated)
                keymix.setInput(2, matte)
                if "channels" in keymix.knobs():
                    keymix["channels"].setValue("rgba")
                if "maskChannel" in keymix.knobs():
                    keymix["maskChannel"].setValue("rgba.alpha")
                if layout_origin is not None:
                    _set_node_position(attenuated, layout_x, layout_y)
                    _set_node_position(matte, layout_x + MATTE_BRANCH_OFFSET, layout_y)
                    _set_node_position(keymix, layout_x, layout_y + NODE_ROW_SPACING)
                    layout_y += 2 * NODE_ROW_SPACING
                current = keymix
                created.extend((matte.name(), attenuated.name(), keymix.name()))
        return current, created

    if layer["gain"] != 1.0:
        grade = nuke.nodes.Grade()
        grade.setName(f"Grade_{index + 1:02d}")
        grade.setInput(0, current)
        grade["multiply"].setValue(layer["gain"])
        if layout_origin is not None:
            _set_node_position(grade, layout_x, layout_y)
            layout_y += NODE_ROW_SPACING
        current = grade
        created.append(grade.name())
    if layer["blur_size"] > 0.0:
        blur = nuke.nodes.Blur()
        blur.setName(f"Blur_{index + 1:02d}")
        blur.setInput(0, current)
        blur["size"].setValue(layer["blur_size"])
        if layout_origin is not None:
            _set_node_position(blur, layout_x, layout_y)
        current = blur
        created.append(blur.name())
    return current, created


def _require_layers(read: Any, required_layers: list[str]) -> None:
    channels = read.channels()
    missing = [
        layer
        for layer in required_layers
        if not any(channel == layer or channel.startswith(f"{layer}.") for channel in channels)
    ]
    if missing:
        raise RuntimeError(f"Nuke EXR is missing required layers: {', '.join(missing)}")


def _set_write_options(write: Any, spec: Mapping[str, Any]) -> None:
    knobs = write.knobs()
    for manifest_key, knob_name in (("output_colorspace", "colorspace"), ("output_datatype", "datatype")):
        value = spec.get(manifest_key)
        if value and knob_name in knobs:
            write[knob_name].setValue(value)


def _set_read_frame_range(read: Any, first: int, last: int) -> None:
    """Apply the manifest range to Read knobs created through the Nuke API."""
    knobs = read.knobs()
    for name, value in (("first", first), ("last", last), ("origfirst", first), ("origlast", last)):
        if name in knobs:
            read[name].setValue(value)


def _connect_merge(merge: Any, background: Any, foreground: Any) -> None:
    """Connect Nuke input 0 as B and input 1 as A; ``minus`` therefore evaluates A-B."""
    merge.setInput(0, background)
    merge.setInput(1, foreground)


def _layer_row_count(layer: Mapping[str, Any]) -> int:
    rows = 1 if layer["channel"] else 0
    if layer["adjustments"]:
        return rows + sum(2 if operation["kind"] == "material_gain" else 1 for operation in layer["adjustments"])
    return rows + int(layer["gain"] != 1.0) + int(layer["blur_size"] > 0.0)


def _set_node_position(node: Any, x: int, y: int) -> None:
    set_xy = getattr(node, "setXYpos", None)
    if callable(set_xy):
        set_xy(x, y)
        return
    set_x = getattr(node, "setXpos", None)
    set_y = getattr(node, "setYpos", None)
    if callable(set_x):
        set_x(x)
    if callable(set_y):
        set_y(y)


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
