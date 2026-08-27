"""Bounded, readback-verified operations for a Nuke node graph."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")
_SENSITIVE_PARTS = ("callback", "password", "python", "script", "secret", "token")
_MAX_VALUE_ITEMS = 64
_MAX_VALUE_DEPTH = 4
_MAX_STRING_LENGTH = 4096


class NodeGraphMutationError(RuntimeError):
    """A stable public-safe graph transaction failure."""


def create_node_handle(
    nuke: Any,
    node_class: str,
    *,
    name: str | None = None,
    x: int | None = None,
    y: int | None = None,
) -> Any:
    """Create one node and recover a uniquely attributable partial mutation."""
    _validate_create_request(nuke, node_class, name=name, x=x, y=y)
    before = _graph_snapshot(nuke)
    created = None
    try:
        created = nuke.createNode(node_class, inpanel=False)
        if name is not None:
            created.setName(name, uncollide=False)
        if x is not None and y is not None:
            created.setXYpos(x, y)

        after = _graph_snapshot(nuke)
        if any(identity not in after for identity in before):
            raise NodeGraphMutationError("pre-existing graph changed during node creation")
        created_identity = _node_identity(created)
        if created_identity not in after:
            raise NodeGraphMutationError("created node is missing from graph readback")
        if created.Class() != node_class:
            raise NodeGraphMutationError("created node class does not match request")
        if name is not None and created.name() != name:
            raise NodeGraphMutationError("created node name does not match request")
        actual_x, actual_y = _node_position(created)
        if x is not None and (actual_x, actual_y) != (x, y):
            raise NodeGraphMutationError("created node position does not match request")
        return created
    except Exception:
        _recover_partial_creation(nuke, before, node_class=node_class, known_node=created)
        raise


def delete_node_handle(nuke: Any, node: Any) -> dict[str, Any]:
    """Delete one captured node and verify removal without reusing its handle."""
    identity = _node_identity(node)
    node_name = str(node.name())
    node_class = str(node.Class())
    nuke.delete(node)
    if identity in _graph_snapshot(nuke):
        raise NodeGraphMutationError("deleted node is still present in graph readback")
    return {"name": node_name, "class": node_class, "deleted": True}


def list_node_graph(nuke: Any, *, max_nodes: int = 256, max_knobs_per_node: int = 64) -> dict[str, Any]:
    """Return bounded node identity, topology, and JSON-safe knob values."""
    _bounded_int("max_nodes", max_nodes, 1, 1000)
    _bounded_int("max_knobs_per_node", max_knobs_per_node, 0, 128)
    all_nodes = list(nuke.allNodes(recurseGroups=True))
    nodes = [_describe_node(node, max_knobs_per_node=max_knobs_per_node) for node in all_nodes[:max_nodes]]
    return {
        "node_count": len(all_nodes),
        "returned_node_count": len(nodes),
        "truncated": len(all_nodes) > max_nodes,
        "nodes": nodes,
    }


def create_node(
    nuke: Any,
    node_class: str,
    *,
    name: str | None = None,
    x: int | None = None,
    y: int | None = None,
) -> dict[str, Any]:
    """Create one available node class without clearing or replacing existing nodes."""
    created = create_node_handle(nuke, node_class, name=name, x=x, y=y)
    actual_x, actual_y = _node_position(created)
    return {"name": created.name(), "class": created.Class(), "x": actual_x, "y": actual_y}


def delete_node(nuke: Any, node_name: str) -> dict[str, Any]:
    """Delete one exact node and verify that it is gone."""
    node = _require_node(nuke, node_name)
    return delete_node_handle(nuke, node)


def connect_input(
    nuke: Any,
    node_name: str,
    input_index: int,
    source_node_name: str | None,
) -> dict[str, Any]:
    """Connect or disconnect one input, rolling back if readback disagrees."""
    _bounded_int("input_index", input_index, 0, 63)
    node = _require_node(nuke, node_name)
    source = None if source_node_name is None else _require_node(nuke, source_node_name)
    max_inputs = int(node.maxInputs())
    if input_index >= max_inputs:
        raise ValueError("input_index exceeds the node input capacity")

    previous = node.input(input_index)
    try:
        node.setInput(input_index, source)
        if node.input(input_index) is not source:
            raise RuntimeError("input connection readback does not match request")
    except Exception:
        node.setInput(input_index, previous)
        raise
    return {"node": node_name, "input_index": input_index, "source": source_node_name}


def get_knob(nuke: Any, node_name: str, knob_name: str) -> dict[str, Any]:
    """Read one non-executable knob as a bounded JSON value."""
    node, knob = _require_knob(nuke, node_name, knob_name)
    knob_class = _knob_class(knob)
    _reject_sensitive_knob(knob_name, knob_class)
    return {
        "node": node.name(),
        "knob": knob_name,
        "knob_class": knob_class,
        "value": _json_value(knob.value()),
    }


def set_knob(nuke: Any, node_name: str, knob_name: str, value: Any) -> dict[str, Any]:
    """Write one static non-executable knob and require a matching readback."""
    node, knob = _require_knob(nuke, node_name, knob_name)
    knob_class = _knob_class(knob)
    _reject_sensitive_knob(knob_name, knob_class, writable=True)
    requested = _json_value(value)
    previous = knob.value()
    try:
        knob.setValue(value)
        actual = _json_value(knob.value())
        if actual != requested:
            raise RuntimeError("knob readback does not match requested value")
    except Exception:
        knob.setValue(previous)
        raise
    return {"node": node.name(), "knob": knob_name, "knob_class": knob_class, "value": actual}


def _describe_node(node: Any, *, max_knobs_per_node: int) -> dict[str, Any]:
    inputs = []
    input_count = min(int(node.inputs()), 64)
    for index in range(input_count):
        source = node.input(index)
        if source is not None:
            inputs.append({"index": index, "node": source.name()})

    values: dict[str, Any] = {}
    omitted = []
    knobs = node.knobs()
    for name in sorted(knobs):
        knob = knobs[name]
        try:
            _reject_sensitive_knob(name, _knob_class(knob))
            if len(values) >= max_knobs_per_node:
                omitted.append(name)
                continue
            values[name] = _json_value(knob.value())
        except (TypeError, ValueError, RuntimeError):
            omitted.append(name)
    return {
        "name": node.name(),
        "class": node.Class(),
        "inputs": inputs,
        "knobs": values,
        "omitted_knobs": omitted,
    }


def _require_node(nuke: Any, node_name: str) -> Any:
    _validate_identifier("node_name", node_name)
    node = nuke.toNode(node_name)
    if node is None:
        raise ValueError("node was not found")
    return node


def _validate_create_request(
    nuke: Any,
    node_class: str,
    *,
    name: str | None,
    x: int | None,
    y: int | None,
) -> None:
    _validate_identifier("node_class", node_class)
    if name is not None:
        _validate_identifier("name", name)
    if (x is None) != (y is None):
        raise ValueError("x and y must be provided together")
    if x is not None:
        _bounded_int("x", x, -1_000_000, 1_000_000)
        _bounded_int("y", y, -1_000_000, 1_000_000)
    if node_class not in set(nuke.allNodeClasses()):
        raise ValueError("node_class is not available in this Nuke host")


def _graph_snapshot(nuke: Any) -> dict[str, Any]:
    nodes = list(nuke.allNodes(recurseGroups=True))
    snapshot = {_node_identity(node): node for node in nodes}
    if len(snapshot) != len(nodes):
        raise NodeGraphMutationError("node graph identity readback is ambiguous")
    return snapshot


def _node_identity(node: Any) -> str:
    full_name = getattr(node, "fullName", None)
    return str(full_name()) if callable(full_name) else str(node.name())


def _recover_partial_creation(
    nuke: Any,
    before: dict[str, Any],
    *,
    node_class: str,
    known_node: Any,
) -> None:
    after = _graph_snapshot(nuke)
    if known_node is not None:
        identity = _node_identity(known_node)
        if identity in after:
            try:
                delete_node_handle(nuke, known_node)
            except Exception:
                raise NodeGraphMutationError(f"{node_class} partial creation rollback failed") from None
        return

    added = [node for identity, node in after.items() if identity not in before]
    if not added:
        return
    if len(added) != 1:
        raise NodeGraphMutationError(f"{node_class} partial creation attribution is ambiguous")
    candidate = added[0]
    try:
        if candidate.Class() != node_class:
            raise NodeGraphMutationError(f"{node_class} partial creation attribution is ambiguous")
        delete_node_handle(nuke, candidate)
    except NodeGraphMutationError:
        raise
    except Exception:
        raise NodeGraphMutationError(f"{node_class} partial creation rollback failed") from None


def _require_knob(nuke: Any, node_name: str, knob_name: str) -> tuple[Any, Any]:
    _validate_identifier("knob_name", knob_name)
    node = _require_node(nuke, node_name)
    knobs = node.knobs()
    if knob_name not in knobs:
        raise ValueError("knob was not found on the node")
    return node, knobs[knob_name]


def _validate_identifier(label: str, value: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} must be a bounded Nuke identifier")


def _bounded_int(label: str, value: int, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")


def _knob_class(knob: Any) -> str:
    method = getattr(knob, "Class", None)
    return str(method()) if callable(method) else type(knob).__name__


def _reject_sensitive_knob(name: str, knob_class: str, *, writable: bool = False) -> None:
    surface = f"{name} {knob_class}".lower()
    if any(part in surface for part in _SENSITIVE_PARTS):
        if writable:
            raise ValueError("executable knob classes are not writable")
        raise ValueError("sensitive or executable knob is not readable")


def _json_value(value: Any, *, depth: int = 0) -> Any:
    if depth > _MAX_VALUE_DEPTH:
        raise ValueError("knob value exceeds the nesting limit")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("knob value is not finite")
        return value
    if isinstance(value, str):
        if len(value) > _MAX_STRING_LENGTH:
            raise ValueError("knob string exceeds the size limit")
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_VALUE_ITEMS:
            raise ValueError("knob mapping exceeds the item limit")
        return {str(key): _json_value(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        if len(value) > _MAX_VALUE_ITEMS:
            raise ValueError("knob sequence exceeds the item limit")
        return [_json_value(item, depth=depth + 1) for item in value]
    raise ValueError("knob value is not JSON-safe")


def _node_position(node: Any) -> tuple[int, int]:
    xpos = getattr(node, "xpos", None)
    ypos = getattr(node, "ypos", None)
    x = xpos() if callable(xpos) else getattr(node, "x", 0)
    y = ypos() if callable(ypos) else getattr(node, "y", 0)
    return int(x), int(y)
