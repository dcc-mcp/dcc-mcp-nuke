"""Render bounded, explicitly owned previews without keeping nodes in the script."""

from __future__ import annotations

import atexit
import hashlib
import math
import os
import struct
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from dcc_mcp_nuke.color_management import set_display_transform
from dcc_mcp_nuke.image_statistics import sample_channel_statistics

MAX_PREVIEWS = 8
MAX_IMAGE_BYTES = 8 * 1024 * 1024
_PREVIEWS: dict[str, tempfile.TemporaryDirectory] = {}


def cleanup_preview(preview_id: str) -> dict[str, Any]:
    """Release only a preview owned by this process; never accept a caller path."""
    if not isinstance(preview_id, str) or preview_id not in _PREVIEWS:
        raise ValueError("preview_id is not owned by this Nuke process")
    _PREVIEWS[preview_id].cleanup()
    del _PREVIEWS[preview_id]
    return {"preview_id": preview_id, "removed": True}


def _cleanup_all() -> None:
    for directory in list(_PREVIEWS.values()):
        directory.cleanup()
    _PREVIEWS.clear()


atexit.register(_cleanup_all)


def render_node_preview(
    nuke: Any,
    *,
    node_name: str,
    frame: int | None = None,
    max_size: int = 512,
    channels: str = "rgba",
    display: str | None = None,
    view: str | None = None,
) -> dict[str, Any]:
    """Render one RGB PNG, a source bbox, and bounded source-channel statistics."""
    if not isinstance(node_name, str) or not node_name.strip():
        raise ValueError("node_name must be a non-empty string")
    if frame is not None and (not isinstance(frame, int) or isinstance(frame, bool)):
        raise ValueError("frame must be an integer")
    if not isinstance(max_size, int) or isinstance(max_size, bool) or not 16 <= max_size <= 1024:
        raise ValueError("max_size must be between 16 and 1024")
    if not isinstance(channels, str) or not channels.strip():
        raise ValueError("channels must be an exact channel or layer name")
    if (display is None) != (view is None):
        raise ValueError("display and view must be supplied together")
    if display is not None and any(not isinstance(value, str) or not value.strip() for value in (display, view)):
        raise ValueError("display and view must be non-empty strings")
    if len(_PREVIEWS) >= MAX_PREVIEWS:
        raise ValueError("preview limit reached; call cleanup_preview before rendering another preview")
    with nuke.root():
        source = nuke.toNode(node_name)
    if source is None:
        raise ValueError("node not found")

    preview_id = uuid.uuid4().hex
    directory = tempfile.TemporaryDirectory(prefix="dcc-mcp-nuke-preview-")
    path = Path(directory.name) / "preview.png"
    try:
        with _temporary_graph(nuke, source, preview_id) as group:
            used_frame = int(nuke.frame()) if frame is None else frame
            nuke.frame(used_frame)
            available = list(source.channels())
            resolved, rgb = _channel_mapping(available, channels)
            width, height = int(source.width()), int(source.height())
            aspect = float(source.pixelAspect())
            if width <= 0 or height <= 0 or not math.isfinite(aspect) or aspect <= 0:
                raise ValueError("source image dimensions and pixel aspect must be positive")
            scale = min(1.0, max_size / max(width * aspect, height))
            output_width = max(1, min(max_size, round(width * aspect * scale)))
            output_height = max(1, min(max_size, round(height * scale)))
            bbox = list(source.bbox())
            with nuke.root():
                statistics = sample_channel_statistics(
                    node_name=source.fullName(), channels=resolved, frame=used_frame, max_samples_per_channel=256
                )
            if not statistics["success"]:
                raise RuntimeError(statistics["error"])

            with group:
                image = nuke.nodes.Input()
            if not group.setInput(0, source) or group.input(0) != source:
                raise RuntimeError("preview Group input could not be connected")
            with group:
                mapped = nuke.nodes.Copy(inputs=[image, image])
                for index, (channel, destination) in enumerate(zip(rgb, ("rgba.red", "rgba.green", "rgba.blue"))):
                    mapped[f"from{index}"].setValue(channel)
                    mapped[f"to{index}"].setValue(destination)
                resized = nuke.nodes.Reformat(
                    inputs=[mapped],
                    type="to box",
                    box_width=output_width,
                    box_height=output_height,
                    box_fixed=True,
                    box_pixel_aspect=1.0,
                    resize="distort",
                    pbb=False,
                )
                image = resized
                if display is not None:
                    image = nuke.nodes.OCIODisplay(inputs=[image])
                    with nuke.root():
                        set_display_transform(nuke, image.fullName(), display, view)
                write = nuke.nodes.Write(
                    inputs=[image], file=path.as_posix(), file_type="png", channels="rgb", raw=True
                )
                nuke.execute(write, used_frame, used_frame, 1)
            image_info = _inspect_png(path, max_size)

        _PREVIEWS[preview_id] = directory
        return {
            "node_name": source.fullName(),
            "frame": used_frame,
            "channels": channels,
            "rgb_mapping": dict(zip(("red", "green", "blue"), rgb)),
            "display_transform": {"display": display, "view": view} if display is not None else None,
            "encoding": "RGB PNG; raw numeric values clipped to [0, 1] unless display/view is supplied",
            "source_bbox": dict(zip(("x", "y", "width", "height"), bbox)),
            "sampling": statistics["context"]["sampling"],
            "statistics": statistics["context"]["statistics"],
            "preview_id": preview_id,
            "owner_pid": os.getpid(),
            "lifetime": "until cleanup_preview or normal host exit",
            "file_path": str(path),
            "file_ref": {"uri": path.as_uri(), "mime_type": "image/png", **image_info},
        }
    except BaseException:
        directory.cleanup()
        raise


def _channel_mapping(available: list[str], requested: str) -> tuple[list[str], list[str]]:
    if requested in available:
        return [requested], [requested] * 3
    resolved = [name for name in available if name.startswith(requested + ".")]
    if not resolved:
        raise ValueError("channel or layer is unavailable")
    if len(resolved) == 1:
        return resolved, resolved * 3
    rgb = [requested + "." + component for component in ("red", "green", "blue")]
    if len(resolved) > 16 or not all(channel in available for channel in rgb):
        raise ValueError("layer must contain RGB channels; request an exact channel for a grayscale preview")
    return resolved, rgb


def _inspect_png(path: Path, max_size: int) -> dict[str, Any]:
    size = path.stat().st_size
    if not 24 <= size <= MAX_IMAGE_BYTES:
        raise RuntimeError("rendered preview is empty or exceeds the 8 MiB limit")
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise RuntimeError("Nuke did not produce a PNG preview")
    width, height = struct.unpack(">II", data[16:24])
    if min(width, height) <= 0 or max(width, height) > max_size:
        raise RuntimeError("rendered preview dimensions exceed the requested size")
    return {"width": width, "height": height, "bytes": size, "digest": "sha256:" + hashlib.sha256(data).hexdigest()}


@contextmanager
def _temporary_graph(nuke: Any, source: Any, preview_id: str):
    root = nuke.root()
    with root:
        nodes = list(nuke.allNodes(recurseGroups=True))
    selection = [(node, node.isSelected()) for node in nodes]
    old_frame, old_proxy, old_modified = nuke.frame(), root.proxy(), root.modified()
    name = "DCC_MCP_PREVIEW_" + preview_id
    parent = source.parent()
    full_name = name if parent == root else parent.fullName() + "." + name
    nuke.Undo.disable()
    try:
        root.setProxy(False)
        with parent:
            group = nuke.nodes.Group(name=name)
        yield group
    finally:
        failures = []
        try:
            with root:
                group = nuke.toNode(full_name)
                if group is not None:
                    nuke.delete(group)
                if nuke.toNode(full_name) is not None:
                    raise RuntimeError("temporary preview Group is still present")
        except Exception as exc:
            failures.append(str(exc))
        for operation in [
            *(lambda node=node, selected=selected: node.setSelected(selected) for node, selected in selection),
            lambda: nuke.frame(old_frame),
            lambda: root.setProxy(old_proxy),
        ]:
            try:
                operation()
            except Exception as exc:
                failures.append(str(exc))
        try:
            root.setModified(True if failures else old_modified)
        finally:
            nuke.Undo.enable()
        if failures:
            raise RuntimeError("preview state restoration failed: " + "; ".join(failures))
