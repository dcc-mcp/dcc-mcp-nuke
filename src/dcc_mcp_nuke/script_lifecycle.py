"""Bounded, readback-verified Nuke script-lifecycle operations.

The host ``nuke`` module is passed in as an argument so this module is pure,
importable, and testable without a running Nuke. Root-settings writes require a
matching readback and roll back on mismatch; ``paste_nodes`` is additive and
fails closed if it would drop a pre-existing node.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")
_MAX_NAME_LENGTH = 256
_MAX_ENUM_ITEMS = 512
_MAX_FRAME = 1_000_000_000
_MAX_FPS = 1000.0


class ScriptLifecycleError(Exception):
    """Public-safe base error for the Nuke script-lifecycle contract."""


class ScriptLifecycleValidationError(ScriptLifecycleError, ValueError):
    """A caller supplied an unsupported or out-of-range value."""


class ScriptLifecycleRuntimeError(ScriptLifecycleError, RuntimeError):
    """The host could not prove the requested postcondition."""


def get_root_settings(nuke: Any, *, max_formats: int = 128) -> dict[str, Any]:
    """Read Root frame range, fps, format, and a bounded enumerated format list."""
    _bounded_int("max_formats", max_formats, 1, _MAX_ENUM_ITEMS)
    first = _read_frame_value(_root_knob(nuke, "first_frame"))
    last = _read_frame_value(_root_knob(nuke, "last_frame"))
    fps = _read_fps_value(_root_knob(nuke, "fps"))
    format_knob = _root_knob(nuke, "format")
    format_value = _read_format_value(format_knob)
    formats, format_count, format_truncated = _describe_formats(nuke, max_formats)
    return {
        "first_frame": first,
        "last_frame": last,
        "fps": fps,
        "format": format_value,
        "available_formats": formats,
        "available_format_count": format_count,
        "available_formats_truncated": format_truncated,
    }


def set_root_settings(
    nuke: Any,
    *,
    first_frame: int | None = None,
    last_frame: int | None = None,
    fps: float | None = None,
    format: str | None = None,
) -> dict[str, Any]:
    """Set one or more Root settings and require a matching readback; reject invalid values."""
    if all(value is None for value in (first_frame, last_frame, fps, format)):
        raise ScriptLifecycleValidationError("at least one root setting must be provided")
    return _apply_root_settings(nuke, first_frame=first_frame, last_frame=last_frame, fps=fps, format=format)


def new_script(
    nuke: Any,
    *,
    discard_unsaved_changes: bool = False,
    first_frame: int | None = None,
    last_frame: int | None = None,
    fps: float | None = None,
    format: str | None = None,
) -> dict[str, Any]:
    """Clear the graph and start a fresh script, applying optional Root settings."""
    if not isinstance(discard_unsaved_changes, bool):
        raise ScriptLifecycleValidationError("discard_unsaved_changes must be a boolean")
    if (first_frame is None) != (last_frame is None):
        raise ScriptLifecycleValidationError("first_frame and last_frame must be provided together")

    # Validate everything that does not depend on the post-clear defaults before
    # clearing, so an obviously bad request never destroys the current graph.
    if first_frame is not None:
        first_frame = _validate_frame("first_frame", first_frame)
    if last_frame is not None:
        last_frame = _validate_frame("last_frame", last_frame)
    if first_frame is not None and last_frame is not None:
        _validate_range(first_frame, last_frame)
    if fps is not None:
        fps = _validate_fps(fps)
    if format is not None:
        _validate_name("format", format)
        _require_format(nuke, format)

    root = nuke.root()
    modified = getattr(root, "modified", None)
    if callable(modified) and modified() and not discard_unsaved_changes:
        raise ScriptLifecycleValidationError("current script has unsaved changes; set discard_unsaved_changes to true")

    try:
        nuke.scriptClear()
    except Exception:
        raise ScriptLifecycleRuntimeError("failed to clear the current Nuke script") from None
    try:
        remaining = list(nuke.allNodes(recurseGroups=True))
    except Exception:
        raise ScriptLifecycleRuntimeError("failed to verify the cleared Nuke script") from None
    if remaining:
        raise ScriptLifecycleRuntimeError("cleared Nuke script still contains nodes")

    if any(value is not None for value in (first_frame, last_frame, fps, format)):
        settings = _apply_root_settings(nuke, first_frame=first_frame, last_frame=last_frame, fps=fps, format=format)
    else:
        settings = get_root_settings(nuke)
    return {"node_count": 0, **settings}


def paste_nodes(nuke: Any, path: str, *, max_reported_nodes: int = 256) -> dict[str, Any]:
    """Paste nodes from an absolute .nk node-paste file without clearing existing nodes."""
    _bounded_int("max_reported_nodes", max_reported_nodes, 1, 1000)
    target = _validate_script_path(path)
    before = _graph_snapshot(nuke)
    # nodePaste connects pasted nodes into any currently-selected node, so deselect
    # first to keep the paste purely additive, then restore the prior selection.
    selection = _capture_selection(nuke)
    _set_selection(selection, False)
    try:
        try:
            nuke.nodePaste(str(target))
        except Exception:
            _recover_added(nuke, before)
            raise ScriptLifecycleRuntimeError("failed to paste Nuke nodes") from None

        after = _graph_snapshot(nuke)
        if any(identity not in after for identity in before):
            _recover_added(nuke, before)
            raise ScriptLifecycleRuntimeError("paste removed pre-existing nodes")
        added_identities = [identity for identity in after if identity not in before]
        if not added_identities:
            raise ScriptLifecycleRuntimeError("paste did not add any nodes")

        added = []
        for identity in added_identities[:max_reported_nodes]:
            node = after[identity]
            added.append({"name": str(node.name()), "class": str(node.Class())})
        return {
            "path": str(target),
            "added_node_count": len(added_identities),
            "returned_added_count": len(added),
            "truncated": len(added_identities) > max_reported_nodes,
            "added_nodes": added,
            "preserved_node_count": len(before),
        }
    finally:
        _restore_selection(selection)


def _apply_root_settings(
    nuke: Any,
    *,
    first_frame: int | None,
    last_frame: int | None,
    fps: float | None,
    format: str | None,
) -> dict[str, Any]:
    current_first = _read_frame_value(_root_knob(nuke, "first_frame"))
    current_last = _read_frame_value(_root_knob(nuke, "last_frame"))
    eff_first = current_first if first_frame is None else first_frame
    eff_last = current_last if last_frame is None else last_frame
    if first_frame is not None:
        first_frame = _validate_frame("first_frame", first_frame)
    if last_frame is not None:
        last_frame = _validate_frame("last_frame", last_frame)
    if fps is not None:
        fps = _validate_fps(fps)
    format_obj = None
    if format is not None:
        _validate_name("format", format)
        format_obj = _require_format(nuke, format)
    _validate_range(eff_first, eff_last)

    # ``requested`` holds the scalar used for readback comparison; ``write_values``
    # holds the value actually written. A Format_Knob is set with a Format object,
    # not the format name string.
    requested: dict[str, Any] = {}
    write_values: dict[str, Any] = {}
    if first_frame is not None:
        requested["first_frame"] = first_frame
        write_values["first_frame"] = first_frame
    if last_frame is not None:
        requested["last_frame"] = last_frame
        write_values["last_frame"] = last_frame
    if fps is not None:
        requested["fps"] = fps
        write_values["fps"] = fps
    if format is not None:
        requested["format"] = format
        write_values["format"] = format_obj

    knobs = {label: _root_knob(nuke, label) for label in requested}
    previous = {label: knob.value() for label, knob in knobs.items()}
    try:
        for label in requested:
            knobs[label].setValue(write_values[label])
        _verify_root_readback(knobs, requested)
    except ScriptLifecycleError:
        _restore_root(knobs, previous)
        raise
    except Exception:
        _restore_root(knobs, previous)
        raise ScriptLifecycleRuntimeError("failed to apply root settings") from None
    return get_root_settings(nuke)


def _verify_root_readback(knobs: dict[str, Any], requested: dict[str, Any]) -> None:
    for label, value in requested.items():
        knob = knobs[label]
        if label in ("first_frame", "last_frame"):
            if _read_frame_value(knob) != value:
                raise ScriptLifecycleRuntimeError("root settings readback does not match request")
        elif label == "fps":
            actual = _read_fps_value(knob)
            if not math.isclose(actual, value, rel_tol=1e-6, abs_tol=1e-9):
                raise ScriptLifecycleRuntimeError("root settings readback does not match request")
        elif _read_format_value(knob) != value:
            raise ScriptLifecycleRuntimeError("root settings readback does not match request")


def _restore_root(knobs: dict[str, Any], previous: dict[str, Any]) -> None:
    """Restore every Root setting, attempting all knobs even if one restore fails.

    A single failing ``setValue`` must not abort the remaining restores: leaving
    other Root settings at the requested values is a partial rollback and a
    data-integrity defect. Accumulate failures and report only after all are tried.
    """
    failed = False
    for label, value in previous.items():
        try:
            knobs[label].setValue(value)
        except Exception:
            failed = True
    if failed:
        raise ScriptLifecycleRuntimeError("root settings rollback failed")


def _require_format(nuke: Any, name: str) -> Any:
    """Return the Format object matching ``name``; reject unknown names.

    Nuke's ``Format_Knob`` has no ``values()``; the enumerated format list comes
    from ``nuke.formats()`` (a list of ``Format`` objects, named via ``Format.name()``).
    """
    formats = _enumerate_format_objects(nuke)
    if not formats:
        raise ScriptLifecycleRuntimeError("Root does not expose an enumerated format list")
    for fmt in formats:
        if _format_name(fmt) == name:
            return fmt
    raise ScriptLifecycleValidationError("format is not in the enumerated format list")


def _read_frame_value(knob: Any) -> int:
    value = _knob_value(knob)
    if isinstance(value, bool):
        raise ScriptLifecycleRuntimeError("frame value is not an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    raise ScriptLifecycleRuntimeError("frame value is not an integer")


def _read_fps_value(knob: Any) -> float:
    value = _knob_value(knob)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScriptLifecycleRuntimeError("fps value is not a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ScriptLifecycleRuntimeError("fps value is not a finite number")
    return result


def _read_format_value(knob: Any) -> Any:
    """Read a Format_Knob value as a format-name string.

    Nuke's ``Format_Knob.value()`` returns a ``Format`` object, not a string; the
    stable public identity is the format name from ``Format.name()``.
    """
    value = _knob_value(knob)
    if value is None:
        return None
    name = _format_name(value)
    if name is not None:
        return name
    if isinstance(value, str):
        return value
    return str(value)


def _knob_value(knob: Any) -> Any:
    value_fn = getattr(knob, "value", None)
    if not callable(value_fn):
        raise ScriptLifecycleRuntimeError("root knob does not expose a readable value")
    try:
        return value_fn()
    except Exception:
        raise ScriptLifecycleRuntimeError("root knob value could not be read") from None


def _format_name(fmt: Any) -> str | None:
    """Return a Nuke ``Format`` object's name, or ``None`` when unreadable."""
    name_fn = getattr(fmt, "name", None)
    if not callable(name_fn):
        return None
    try:
        name = name_fn()
    except Exception:
        return None
    if isinstance(name, str) and name and len(name) <= _MAX_NAME_LENGTH:
        return name
    return None


def _enumerate_format_objects(nuke: Any) -> list[Any]:
    """Enumerate ``Format`` objects from ``nuke.formats()``, dropping unreadable items."""
    formats_fn = getattr(nuke, "formats", None)
    if not callable(formats_fn):
        return []
    try:
        raw = formats_fn()
    except Exception:
        return []
    if not isinstance(raw, (list, tuple)):
        return []
    return [fmt for fmt in raw if _format_name(fmt) is not None]


def _enumerate_format_names(nuke: Any) -> list[str]:
    names: list[str] = []
    for fmt in _enumerate_format_objects(nuke):
        name = _format_name(fmt)
        if name is not None:
            names.append(name)
    return list(dict.fromkeys(names))


def _describe_formats(nuke: Any, cap: int) -> tuple[list[str], int, bool]:
    names = _enumerate_format_names(nuke)
    total = len(names)
    return names[:cap], total, total > cap


def _capture_selection(nuke: Any) -> dict[Any, bool]:
    """Capture each node's ``selected`` knob state (gizmos.py selection idiom)."""
    selected: dict[Any, bool] = {}
    try:
        nodes = list(nuke.allNodes(recurseGroups=True))
    except Exception:
        return selected
    for node in nodes:
        knob_fn = getattr(node, "knob", None)
        if not callable(knob_fn):
            continue
        try:
            knob = knob_fn("selected")
        except Exception:
            continue
        if knob is None:
            continue
        try:
            selected[node] = bool(knob.value())
        except Exception:
            continue
    return selected


def _set_selection(selection: dict[Any, bool], value: bool) -> None:
    for node in selection:
        setter = getattr(node, "setSelected", None)
        if not callable(setter):
            continue
        try:
            setter(value)
        except Exception:
            continue


def _restore_selection(selection: dict[Any, bool]) -> None:
    for node, was_selected in selection.items():
        setter = getattr(node, "setSelected", None)
        if not callable(setter):
            continue
        try:
            setter(was_selected)
        except Exception:
            # Best-effort: a changed or deleted node must not mask the paste result.
            continue


def _validate_script_path(path: Any) -> Path:
    if not isinstance(path, str) or not path:
        raise ScriptLifecycleValidationError("path must be a non-empty string")
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ScriptLifecycleValidationError("path must be absolute")
    target = candidate.resolve()
    if target.suffix.lower() != ".nk":
        raise ScriptLifecycleValidationError("script path must end in .nk")
    if not target.exists():
        raise ScriptLifecycleValidationError("script does not exist")
    if not target.is_file():
        raise ScriptLifecycleValidationError("script path is not a file")
    return target


def _graph_snapshot(nuke: Any) -> dict[str, Any]:
    nodes = list(nuke.allNodes(recurseGroups=True))
    snapshot = {_node_identity(node): node for node in nodes}
    if len(snapshot) != len(nodes):
        raise ScriptLifecycleRuntimeError("node graph identity readback is ambiguous")
    return snapshot


def _node_identity(node: Any) -> str:
    full_name = getattr(node, "fullName", None)
    return str(full_name()) if callable(full_name) else str(node.name())


def _recover_added(nuke: Any, before: dict[str, Any]) -> None:
    try:
        after = _graph_snapshot(nuke)
    except Exception:
        return
    for identity, node in after.items():
        if identity not in before:
            try:
                nuke.delete(node)
            except Exception:
                continue


def _root_knob(nuke: Any, name: str) -> Any:
    root = nuke.root()
    getter = getattr(root, "__getitem__", None)
    if getter is None:
        raise ScriptLifecycleRuntimeError(f"Root does not expose the {name} knob")
    try:
        return root[name]
    except Exception:
        raise ScriptLifecycleRuntimeError(f"Root does not expose the {name} knob") from None


def _validate_frame(label: str, value: int) -> int:
    # Nuke frame numbers may be negative (e.g. handles before frame 0), so bound
    # symmetrically rather than requiring a non-negative frame.
    if isinstance(value, bool) or not isinstance(value, int) or not -_MAX_FRAME <= value <= _MAX_FRAME:
        raise ScriptLifecycleValidationError(f"{label} must be an integer between -{_MAX_FRAME} and {_MAX_FRAME}")
    return value


def _validate_range(first: int, last: int) -> None:
    if first > last:
        raise ScriptLifecycleValidationError("first_frame must be less than or equal to last_frame")


def _validate_fps(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScriptLifecycleValidationError(f"fps must be a finite number between 0 and {_MAX_FPS}")
    result = float(value)
    if not math.isfinite(result) or not 0 < result <= _MAX_FPS:
        raise ScriptLifecycleValidationError(f"fps must be a finite number between 0 and {_MAX_FPS}")
    return result


def _validate_name(label: str, value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > _MAX_NAME_LENGTH:
        raise ScriptLifecycleValidationError(
            f"{label} must be a non-empty name of at most {_MAX_NAME_LENGTH} characters"
        )


def _bounded_int(label: str, value: int, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ScriptLifecycleValidationError(f"{label} must be between {minimum} and {maximum}")


__all__ = [
    "ScriptLifecycleError",
    "ScriptLifecycleRuntimeError",
    "ScriptLifecycleValidationError",
    "get_root_settings",
    "new_script",
    "paste_nodes",
    "set_root_settings",
]
