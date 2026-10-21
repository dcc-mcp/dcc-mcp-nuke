"""Bounded, readback-verified Nuke color-management operations.

The host ``nuke`` module is passed in as an argument so this module is pure,
importable, and testable without a running Nuke. Every write validates the
requested name against an enumerated list coming from the host before applying
it, then requires a matching readback; a mismatch rolls the knob back.
"""

from __future__ import annotations

import re
from typing import Any

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")
_MAX_NAME_LENGTH = 256
_MAX_ENUM_ITEMS = 512
_MAX_COLORSPACES = 512


class ColorManagementError(Exception):
    """Public-safe base error for the Nuke color-management contract."""


class ColorManagementValidationError(ColorManagementError, ValueError):
    """A caller supplied an unsupported or out-of-range value."""


class ColorManagementRuntimeError(ColorManagementError, RuntimeError):
    """The host could not prove the requested postcondition."""


def describe_color_management(
    nuke: Any,
    *,
    max_colorspaces: int = 256,
    max_ocio_configs: int = 128,
) -> dict[str, Any]:
    """Return the active Root color-management mode, config, and enumerations."""
    _bounded_int("max_colorspaces", max_colorspaces, 1, _MAX_COLORSPACES)
    _bounded_int("max_ocio_configs", max_ocio_configs, 1, _MAX_ENUM_ITEMS)

    color_management = _optional_root_value(nuke, "colorManagement")
    working_space = _optional_root_value(nuke, "workingSpaceLUT")

    ocio_config: Any = None
    ocio_values: list[str] = []
    ocio_count = 0
    ocio_truncated = False
    try:
        ocio_knob = _root_knob(nuke, "OCIOConfig")
        ocio_config = _coerce_scalar(_knob_value(ocio_knob))
        enum = _knob_enum(ocio_knob, max_ocio_configs)
        if enum is not None:
            ocio_values = enum["values"]
            ocio_count = enum["count"]
            ocio_truncated = enum["truncated"]
    except ColorManagementError:
        # OCIO config is unavailable outside OCIO color management; reflect it
        # as an empty enumeration instead of failing the whole read.
        pass

    colorspaces, colorspaces_available, colorspace_count, colorspace_truncated = _describe_colorspaces(
        nuke, max_colorspaces
    )

    return {
        "color_management": color_management,
        "ocio_config": ocio_config,
        "available_ocio_configs": ocio_values,
        "available_ocio_config_count": ocio_count,
        "available_ocio_configs_truncated": ocio_truncated,
        "working_space": working_space,
        "colorspaces": colorspaces,
        "colorspaces_available": colorspaces_available,
        "colorspace_count": colorspace_count,
        "colorspaces_truncated": colorspace_truncated,
    }


def set_ocio_config(nuke: Any, config: str) -> dict[str, Any]:
    """Set the Root OCIO config from the enumerated preset list; reject unknown names."""
    _validate_name("config", config)
    knob = _root_knob(nuke, "OCIOConfig")
    enum = _knob_enum(knob, _MAX_ENUM_ITEMS)
    if enum is None or not enum["values"]:
        raise ColorManagementValidationError("OCIO config presets are not enumerable in this Nuke host")
    if config not in enum["values"]:
        raise ColorManagementValidationError("config is not in the enumerated OCIO config list")

    previous = _knob_value(knob)
    try:
        knob.setValue(config)
        if _knob_value(knob) != config:
            raise ColorManagementRuntimeError("OCIO config readback does not match request")
    except ColorManagementError:
        _restore(knob, previous)
        raise
    except Exception:
        _restore(knob, previous)
        raise ColorManagementRuntimeError("failed to set the OCIO config") from None
    return {"ocio_config": config, "color_management": _optional_root_value(nuke, "colorManagement")}


def set_read_colorspace(nuke: Any, node_name: str, colorspace: str) -> dict[str, Any]:
    """Set one Read node's colorspace from its enumerated list; reject unknown names."""
    return _set_node_colorspace(nuke, node_name, "Read", colorspace)


def set_write_colorspace(nuke: Any, node_name: str, colorspace: str) -> dict[str, Any]:
    """Set one Write node's colorspace from its enumerated list; reject unknown names."""
    return _set_node_colorspace(nuke, node_name, "Write", colorspace)


def set_display_transform(nuke: Any, node_name: str, display: str, view: str) -> dict[str, Any]:
    """Set one OCIODisplay node's display and view from enumerated lists; reject unknown names."""
    _validate_name("display", display)
    _validate_name("view", view)
    node = _require_node(nuke, node_name)
    if str(node.Class()) != "OCIODisplay":
        raise ColorManagementValidationError("node must be an OCIODisplay node")

    knobs = _node_knobs(node)
    for name in ("display", "view"):
        if name not in knobs:
            raise ColorManagementRuntimeError("OCIODisplay node does not expose the display/view knobs")
    display_knob = knobs["display"]
    view_knob = knobs["view"]

    display_enum = _knob_enum(display_knob, _MAX_ENUM_ITEMS)
    if display_enum is None or not display_enum["values"]:
        raise ColorManagementRuntimeError("OCIODisplay does not expose an enumerated display list")
    if display not in display_enum["values"]:
        raise ColorManagementValidationError("display is not in the enumerated display list")

    previous_display = _knob_value(display_knob)
    previous_view = _knob_value(view_knob)
    try:
        display_knob.setValue(display)
        if _knob_value(display_knob) != display:
            raise ColorManagementRuntimeError("display readback does not match request")
        # Views are display-dependent: re-read the view enum only after the
        # requested display has been applied and verified.
        view_enum = _knob_enum(view_knob, _MAX_ENUM_ITEMS)
        if view_enum is None or not view_enum["values"]:
            raise ColorManagementRuntimeError("OCIODisplay does not expose an enumerated view list")
        if view not in view_enum["values"]:
            raise ColorManagementValidationError("view is not in the enumerated view list")
        view_knob.setValue(view)
        if _knob_value(view_knob) != view:
            raise ColorManagementRuntimeError("view readback does not match request")
    except ColorManagementError:
        _restore_display_view(display_knob, previous_display, view_knob, previous_view)
        raise
    except Exception:
        _restore_display_view(display_knob, previous_display, view_knob, previous_view)
        raise ColorManagementRuntimeError("failed to set the display transform") from None
    return {"node": node_name, "class": "OCIODisplay", "display": display, "view": view}


def _restore_display_view(display_knob: Any, previous_display: Any, view_knob: Any, previous_view: Any) -> None:
    """Roll back display then view, attempting both even if one restore fails.

    Display is restored first: the view enumeration is display-dependent, so the
    previous view is only valid once the previous display is back in place. A
    failing view restore must not leave the display modified.
    """
    failure: ColorManagementError | None = None
    try:
        _restore(display_knob, previous_display)
    except ColorManagementError as exc:
        failure = exc
    try:
        _restore(view_knob, previous_view)
    except ColorManagementError as exc:
        if failure is None:
            failure = exc
    if failure is not None:
        raise failure


def _set_node_colorspace(nuke: Any, node_name: str, node_class: str, colorspace: str) -> dict[str, Any]:
    _validate_name("colorspace", colorspace)
    node = _require_node(nuke, node_name)
    if str(node.Class()) != node_class:
        raise ColorManagementValidationError(f"node must be a {node_class} node")
    knob = _require_node_knob(node, "colorspace")
    enum = _knob_enum(knob, _MAX_ENUM_ITEMS)
    if enum is None or not enum["values"]:
        raise ColorManagementRuntimeError("node does not expose an enumerated colorspace list")
    if colorspace not in enum["values"]:
        raise ColorManagementValidationError("colorspace is not in the enumerated colorspace list")

    previous = _knob_value(knob)
    try:
        knob.setValue(colorspace)
        if _knob_value(knob) != colorspace:
            raise ColorManagementRuntimeError("colorspace readback does not match request")
    except ColorManagementError:
        _restore(knob, previous)
        raise
    except Exception:
        _restore(knob, previous)
        raise ColorManagementRuntimeError("failed to set the colorspace") from None
    return {"node": node_name, "class": node_class, "colorspace": colorspace, "available_colorspaces": enum["count"]}


def _describe_colorspaces(nuke: Any, cap: int) -> tuple[list[str], bool, int, bool]:
    names = _enumerate_colorspaces(nuke)
    total = len(names)
    return names[:cap], bool(names), total, total > cap


def _enumerate_colorspaces(nuke: Any) -> list[str]:
    """Enumerate global colorspaces via ``nuke.getOcioColorSpaces()``.

    ``nuke.colorspaces`` is not callable, and ``nuke.getColorspaceList`` needs a
    knob rather than the Root. The documented ``list[str]`` surface is
    ``nuke.getOcioColorSpaces()``, which needs no argument.
    """
    factory = getattr(nuke, "getOcioColorSpaces", None)
    if not callable(factory):
        return []
    try:
        raw = factory()
    except Exception:
        return []
    if not isinstance(raw, (list, tuple)):
        return []
    return _clean_names(raw)


def _knob_enum(knob: Any, cap: int) -> dict[str, Any] | None:
    """Return a bounded enumeration for an enum knob, or ``None`` if not enumerable."""
    values_fn = getattr(knob, "values", None)
    if not callable(values_fn):
        return None
    try:
        raw = values_fn()
    except Exception:
        return None
    if not isinstance(raw, (list, tuple)):
        return None
    names = _clean_names(raw)
    total = len(names)
    return {"values": names[:cap], "count": total, "truncated": total > cap}


def _knob_value(knob: Any) -> Any:
    value_fn = getattr(knob, "value", None)
    if not callable(value_fn):
        raise ColorManagementRuntimeError("color-management knob does not expose a readable value")
    return value_fn()


def _restore(knob: Any, previous: Any) -> None:
    try:
        knob.setValue(previous)
    except Exception:
        raise ColorManagementRuntimeError("color-management rollback failed") from None


def _coerce_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else str(value)
    return str(value)


def _optional_root_value(nuke: Any, name: str) -> Any:
    try:
        knob = _root_knob(nuke, name)
    except ColorManagementError:
        return None
    try:
        return _coerce_scalar(_knob_value(knob))
    except ColorManagementError:
        return None


def _root_knob(nuke: Any, name: str) -> Any:
    root = nuke.root()
    getter = getattr(root, "__getitem__", None)
    if getter is None:
        raise ColorManagementValidationError(f"Root does not expose the {name} color-management knob")
    try:
        return root[name]
    except Exception:
        raise ColorManagementValidationError(f"Root does not expose the {name} color-management knob") from None


def _require_node(nuke: Any, node_name: str) -> Any:
    _validate_identifier("node_name", node_name)
    node = nuke.toNode(node_name)
    if node is None:
        raise ColorManagementValidationError("node was not found")
    return node


def _node_knobs(node: Any) -> dict[str, Any]:
    knobs_fn = getattr(node, "knobs", None)
    knobs = knobs_fn() if callable(knobs_fn) else None
    if not isinstance(knobs, dict):
        raise ColorManagementRuntimeError("node does not expose a knob mapping")
    return knobs


def _require_node_knob(node: Any, knob_name: str) -> Any:
    knobs = _node_knobs(node)
    if knob_name not in knobs:
        raise ColorManagementValidationError(f"node does not expose a {knob_name} knob")
    return knobs[knob_name]


def _clean_names(raw: Any) -> list[str]:
    names: list[str] = []
    for item in raw:
        if isinstance(item, str) and item and len(item) <= _MAX_NAME_LENGTH:
            names.append(item)
    return list(dict.fromkeys(names))


def _validate_name(label: str, value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > _MAX_NAME_LENGTH:
        raise ColorManagementValidationError(
            f"{label} must be a non-empty name of at most {_MAX_NAME_LENGTH} characters"
        )


def _validate_identifier(label: str, value: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ColorManagementValidationError(f"{label} must be a bounded Nuke identifier")


def _bounded_int(label: str, value: int, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ColorManagementValidationError(f"{label} must be between {minimum} and {maximum}")


__all__ = [
    "ColorManagementError",
    "ColorManagementRuntimeError",
    "ColorManagementValidationError",
    "describe_color_management",
    "set_display_transform",
    "set_ocio_config",
    "set_read_colorspace",
    "set_write_colorspace",
]
