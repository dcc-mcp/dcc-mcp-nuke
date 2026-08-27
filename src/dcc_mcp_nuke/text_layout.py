"""Bounded, readback-verified Text2 label layout operations."""

from __future__ import annotations

import copy
import math
import re
from typing import Any

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")
_REQUIRED_KNOBS = ("message", "global_font_scale", "box", "xjustify", "yjustify")
_HORIZONTAL_JUSTIFY = frozenset({"left", "center", "right"})
_VERTICAL_JUSTIFY = frozenset({"top", "center", "bottom", "baseline"})
_FONT_REFERENCE_PX = 64.0


class TextLayoutError(Exception):
    """Public-safe base error for the Text2 layout contract."""


class TextLayoutValidationError(TextLayoutError, ValueError):
    """A caller supplied an unsupported or out-of-range value."""


class TextLayoutRuntimeError(TextLayoutError, RuntimeError):
    """The host could not prove the requested postcondition."""


def upsert_text2_label(
    nuke: Any,
    *,
    node_name: str,
    text: str,
    font_size_px: float,
    x: int,
    y: int,
    box_x: float,
    box_y: float,
    box_width: float,
    box_height: float,
    horizontal_justify: str = "left",
    vertical_justify: str = "baseline",
) -> dict[str, Any]:
    """Create or update one Text2 label and require exact layout readback."""
    _validate_identifier("node_name", node_name)
    if not isinstance(text, str) or not 1 <= len(text) <= 4096:
        raise TextLayoutValidationError("text must contain between 1 and 4096 characters")
    font_size_px = _bounded_number("font_size_px", font_size_px, 1.0, 4096.0)
    _bounded_int("x", x, -1_000_000, 1_000_000)
    _bounded_int("y", y, -1_000_000, 1_000_000)
    box_x = _bounded_number("box_x", box_x, -1_000_000.0, 1_000_000.0)
    box_y = _bounded_number("box_y", box_y, -1_000_000.0, 1_000_000.0)
    box_width = _bounded_number("box_width", box_width, 1.0, 1_000_000.0)
    box_height = _bounded_number("box_height", box_height, 1.0, 1_000_000.0)
    if horizontal_justify not in _HORIZONTAL_JUSTIFY:
        raise TextLayoutValidationError("horizontal_justify is not supported")
    if vertical_justify not in _VERTICAL_JUSTIFY:
        raise TextLayoutValidationError("vertical_justify is not supported")

    requested = {
        "text": text,
        "global_font_scale": font_size_px / _FONT_REFERENCE_PX,
        "box": [box_x, box_y, box_x + box_width, box_y + box_height],
        "horizontal_justify": horizontal_justify,
        "vertical_justify": vertical_justify,
    }

    node = None
    created = False
    previous = None
    try:
        node = nuke.toNode(node_name)
        created = node is None
        if node is not None and node.Class() != "Text2":
            raise TextLayoutValidationError("existing node must be a Text2 node")
        if created:
            node = nuke.createNode("Text2", inpanel=False)
            node.setName(node_name, uncollide=False)
        assert node is not None
        knobs = node.knobs()
        if any(name not in knobs for name in _REQUIRED_KNOBS):
            raise TextLayoutValidationError("Text2 node does not expose the required layout knobs")
        if not created:
            previous = _snapshot(node, knobs)

        knobs["message"].setValue(text)
        knobs["global_font_scale"].setValue(requested["global_font_scale"])
        knobs["box"].setValue(requested["box"])
        knobs["xjustify"].setValue(horizontal_justify)
        knobs["yjustify"].setValue(vertical_justify)
        node.setXYpos(x, y)

        if nuke.toNode(node_name) is not node or node.name() != node_name or node.Class() != "Text2":
            raise TextLayoutRuntimeError("Text2 label identity readback does not match request")
        actual = _readback(node)
        if actual != requested or (int(node.xpos()), int(node.ypos())) != (x, y):
            raise TextLayoutRuntimeError("Text2 label readback does not match request")
    except Exception as exc:
        _rollback(nuke, node, created=created, previous=previous)
        if isinstance(exc, TextLayoutError):
            raise
        raise TextLayoutRuntimeError("failed to apply Text2 label layout") from None

    return {
        "created": created,
        "node": {"name": node_name, "class": "Text2", "x": x, "y": y},
        "layout": {
            "text": actual["text"],
            "font_size_px": actual["global_font_scale"] * _FONT_REFERENCE_PX,
            "global_font_scale": actual["global_font_scale"],
            "box": {
                "x": actual["box"][0],
                "y": actual["box"][1],
                "width": actual["box"][2] - actual["box"][0],
                "height": actual["box"][3] - actual["box"][1],
            },
            "horizontal_justify": actual["horizontal_justify"],
            "vertical_justify": actual["vertical_justify"],
        },
    }


def _snapshot(node: Any, knobs: dict[str, Any]) -> dict[str, Any]:
    return {
        "values": {name: copy.deepcopy(knobs[name].value()) for name in _REQUIRED_KNOBS},
        "x": int(node.xpos()),
        "y": int(node.ypos()),
    }


def _readback(node: Any) -> dict[str, Any]:
    knobs = node.knobs()
    box = knobs["box"].value()
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise TextLayoutRuntimeError("Text2 label readback does not match request")
    return {
        "text": knobs["message"].value(),
        "global_font_scale": knobs["global_font_scale"].value(),
        "box": list(box),
        "horizontal_justify": knobs["xjustify"].value(),
        "vertical_justify": knobs["yjustify"].value(),
    }


def _rollback(nuke: Any, node: Any, *, created: bool, previous: dict[str, Any] | None) -> None:
    try:
        if node is None:
            return
        if created:
            nuke.delete(node)
            if nuke.toNode(node.name()) is node:
                raise TextLayoutRuntimeError("Text2 label rollback failed")
            return
        if previous is None:
            return
        knobs = node.knobs()
        for name, value in previous["values"].items():
            knobs[name].setValue(value)
        node.setXYpos(previous["x"], previous["y"])
        if _snapshot(node, knobs) != previous:
            raise TextLayoutRuntimeError("Text2 label rollback failed")
    except Exception:
        raise TextLayoutRuntimeError("Text2 label rollback failed") from None


def _validate_identifier(label: str, value: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise TextLayoutValidationError(f"{label} must be a bounded Nuke identifier")


def _bounded_int(label: str, value: int, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise TextLayoutValidationError(f"{label} must be an integer between {minimum} and {maximum}")


def _bounded_number(label: str, value: float, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TextLayoutValidationError(f"{label} must be a finite number between {minimum} and {maximum}")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise TextLayoutValidationError(f"{label} must be a finite number between {minimum} and {maximum}")
    return result
