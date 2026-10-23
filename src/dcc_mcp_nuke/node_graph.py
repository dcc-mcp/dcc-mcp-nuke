"""Bounded, readback-verified operations for a Nuke node graph."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from graphlib import CycleError, TopologicalSorter
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
        try:
            created = nuke.createNode(node_class, inpanel=False)
        except RuntimeError as exc:
            # Nuke has no API that lists creatable classes; an unavailable class makes
            # createNode raise "<Class>: Unknown command" without adding a node.
            if _is_unknown_command(exc) and _graph_snapshot(nuke).keys() == before.keys():
                raise ValueError("node_class is not available in this Nuke host") from None
            raise
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


def auto_layout_nodes(nuke: Any, node_names: list[str]) -> dict[str, Any]:
    """Autoplace exact nodes in one Group, restoring the graph on failure."""
    if not isinstance(node_names, list) or not 1 <= len(node_names) <= 256:
        raise ValueError("node_names must contain between 1 and 256 exact node names")
    for name in node_names:
        _validate_identifier("node_names item", name)
    if len(set(node_names)) != len(node_names):
        raise ValueError("node_names must not contain duplicates")

    root = nuke.root()
    with root:
        before = _layout_snapshot(nuke)
        targets = [_require_node(nuke, name) for name in node_names]
        if any(name not in before or before[name]["node"] != node for name, node in zip(node_names, targets)):
            raise ValueError("node_names must use exact full names from the graph listing")
        parent = _layout_parent(targets[0])
        if any(_layout_parent(node) != parent for node in targets):
            raise ValueError("auto-layout nodes must share the same parent Group")
        for node in targets:
            if node.Class() == "BackdropNode":
                raise ValueError("auto-layout does not support BackdropNode targets")
            if not callable(getattr(node, "autoplace", None)):
                raise NodeGraphMutationError("native node autoplace is unavailable")
            _require_unlocked_layout_node(node)
        ancestor = parent
        for _ in range(65):
            _require_unlocked_layout_node(ancestor)
            if ancestor == root:
                break
            ancestor = _layout_parent(ancestor)
        else:
            raise ValueError("auto-layout Group nesting exceeds the supported limit")
        placement_order = _layout_order(before, node_names)
        rectangle_nodes = {
            name: state["node"]
            for name, state in before.items()
            if state["class"] != "BackdropNode" and _layout_parent(state["node"]) == parent
        }
        # Nuke's first placement can otherwise use a stale default node height.
        # Read every target's real screen dimensions before placing any node.
        _layout_rectangles(rectangle_nodes)
        modified = root.modified()
        if type(modified) is not bool or not callable(getattr(root, "setModified", None)):
            raise NodeGraphMutationError("script modified state could not be verified")

        try:
            with parent:
                for name in placement_order:
                    before[name]["node"].autoplace()
            _restore_layout_selection(before)
            after = _layout_snapshot(nuke)
            _verify_layout_preservation(before, after, set(node_names))
            rectangles = _layout_rectangles(rectangle_nodes)
            _verify_layout_rectangles(rectangles, set(node_names))
            changed_count = sum(before[name]["position"] != after[name]["position"] for name in node_names)
            root.setModified(modified or bool(changed_count))
            if root.modified() is not (modified or bool(changed_count)):
                raise NodeGraphMutationError("script modified state readback failed")
        except Exception as exc:
            _rollback_layout(nuke, before, modified)
            reason = str(exc) if isinstance(exc, NodeGraphMutationError) else "native auto-layout failed"
            raise NodeGraphMutationError(reason + "; original graph layout restored") from None

        return {
            "algorithm": "nuke_native_autoplace",
            "parent": "root" if parent == root else _node_identity(parent),
            "node_count": len(targets),
            "changed_count": changed_count,
            "placement_order": placement_order,
            "nodes": [
                {
                    "name": node.name(),
                    "full_name": name,
                    "before": {"x": before[name]["position"][0], "y": before[name]["position"][1]},
                    "x": after[name]["position"][0],
                    "y": after[name]["position"][1],
                    "width": rectangles[name][2],
                    "height": rectangles[name][3],
                }
                for name, node in zip(node_names, targets)
            ],
        }


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
    x, y = _node_position(node)
    inputs = []
    input_count = min(int(node.inputs()), 64)
    for index in range(input_count):
        source = node.input(index)
        if source is not None:
            inputs.append({"index": index, "node": source.name()})

    values: dict[str, Any] = {}
    omitted = []
    omitted_knob_count = 0
    knobs = node.knobs()
    for name in sorted(knobs):
        knob = knobs[name]
        try:
            _reject_sensitive_knob(name, _knob_class(knob))
            if len(values) >= max_knobs_per_node:
                # Truncated only by max_knobs_per_node: report a bounded count instead
                # of enumerating every remaining name, so "topology only" calls
                # (max_knobs_per_node=0) cannot expand omitted_knobs to the full
                # knob name list and exceed MCP result limits.
                omitted_knob_count += 1
                continue
            values[name] = _json_value(knob.value())
        except (TypeError, ValueError, RuntimeError):
            # Security-rejected and unreadable knobs stay enumerated by name so
            # fail-closed omissions remain transparent (their count is naturally small).
            omitted.append(name)
    return {
        "name": node.name(),
        "full_name": _node_identity(node),
        "class": node.Class(),
        "x": x,
        "y": y,
        "inputs": inputs,
        "knobs": values,
        "omitted_knobs": omitted,
        "omitted_knob_count": omitted_knob_count,
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


def _is_unknown_command(exc: RuntimeError) -> bool:
    return str(exc).strip().lower().endswith("unknown command")


def _graph_snapshot(nuke: Any) -> dict[str, Any]:
    nodes = list(nuke.allNodes(recurseGroups=True))
    snapshot = {_node_identity(node): node for node in nodes}
    if len(snapshot) != len(nodes):
        raise NodeGraphMutationError("node graph identity readback is ambiguous")
    return snapshot


def _node_identity(node: Any) -> str:
    full_name = getattr(node, "fullName", None)
    return str(full_name()) if callable(full_name) else str(node.name())


def _layout_snapshot(nuke: Any) -> dict[str, Any]:
    graph = _graph_snapshot(nuke)
    if len(graph) > 1000:
        raise ValueError("auto-layout supports graphs with at most 1000 nodes")
    snapshot = {}
    for name, node in graph.items():
        if not callable(getattr(node, "setXYpos", None)) or not callable(getattr(node, "setSelected", None)):
            raise NodeGraphMutationError("node layout state cannot be restored")
        selected = node.isSelected()
        if type(selected) is not bool:
            raise NodeGraphMutationError("node selection state could not be verified")
        count = node.inputs()
        _bounded_int("auto-layout node input count", count, 0, 64)
        snapshot[name] = {
            "node": node,
            "class": node.Class(),
            "position": _layout_position(node),
            "selected": selected,
            "inputs": tuple(node.input(index) for index in range(count)),
        }
    return snapshot


def _layout_position(node: Any) -> tuple[int, int]:
    readers = (getattr(node, "xpos", None), getattr(node, "ypos", None))
    if not all(callable(reader) for reader in readers):
        raise NodeGraphMutationError("native node position readback is unavailable")
    position = tuple(reader() for reader in readers)
    for value in position:
        _bounded_int("auto-layout node coordinate", value, -1_000_000, 1_000_000)
    return position


def _require_unlocked_layout_node(node: Any) -> None:
    probe = getattr(node, "locked", None)
    if not callable(probe) or type(locked := probe()) is not bool:
        raise NodeGraphMutationError("node lock state could not be verified")
    if locked:
        raise ValueError("auto-layout does not support locked nodes or Groups")


def _layout_parent(node: Any) -> Any:
    reader = getattr(node, "parent", None)
    if not callable(reader) or (parent := reader()) is None:
        raise NodeGraphMutationError("node parent Group could not be verified")
    return parent


def _layout_order(snapshot: dict[str, Any], node_names: list[str]) -> list[str]:
    names = set(node_names)
    dependencies = {
        name: sorted(
            {
                _node_identity(source)
                for source in snapshot[name]["inputs"]
                if source is not None and _node_identity(source) in names
            }
        )
        for name in sorted(names)
    }
    try:
        return list(TopologicalSorter(dependencies).static_order())
    except CycleError:
        raise ValueError("auto-layout target connections contain a cycle") from None


def _layout_rectangles(nodes: dict[str, Any]) -> dict[str, tuple[int, int, int, int]]:
    rectangles = {}
    for name, node in nodes.items():
        readers = (getattr(node, "screenWidth", None), getattr(node, "screenHeight", None))
        if not all(callable(reader) for reader in readers):
            raise NodeGraphMutationError("native node screen dimensions are unavailable")
        width, height = (reader() for reader in readers)
        _bounded_int("auto-layout node screen width", width, 1, 10000)
        _bounded_int("auto-layout node screen height", height, 1, 10000)
        x, y = _layout_position(node)
        rectangles[name] = (x, y, width, height)
    return rectangles


def _verify_layout_rectangles(rectangles: dict[str, tuple[int, int, int, int]], targets: set[str]) -> None:
    items = list(rectangles.items())
    for index, (name, (x, y, width, height)) in enumerate(items):
        for other_name, (other_x, other_y, other_width, other_height) in items[index + 1 :]:
            if name not in targets and other_name not in targets:
                continue
            if (
                x < other_x + other_width
                and other_x < x + width
                and y < other_y + other_height
                and other_y < y + height
            ):
                raise NodeGraphMutationError("native auto-layout left overlapping node rectangles")


def _restore_layout_selection(snapshot: dict[str, Any]) -> None:
    for state in snapshot.values():
        if state["node"].isSelected() is not state["selected"]:
            state["node"].setSelected(state["selected"])


def _verify_layout_preservation(before: dict[str, Any], after: dict[str, Any], targets: set[str]) -> None:
    if before.keys() != after.keys():
        raise NodeGraphMutationError("node graph membership changed during auto-layout")
    for name, previous in before.items():
        actual = after[name]
        if any(previous[key] != actual[key] for key in ("node", "class", "inputs", "selected")):
            raise NodeGraphMutationError("node graph topology or selection changed during auto-layout")
        if name not in targets and previous["position"] != actual["position"]:
            raise NodeGraphMutationError("native auto-layout moved an unrequested node")


def _rollback_layout(nuke: Any, before: dict[str, Any], modified: bool) -> None:
    failures = []
    for state in before.values():
        node = state["node"]
        try:
            # Clear any unexpected new input slots before restoring original links.
            count = node.inputs()
            _bounded_int("auto-layout node input count", count, 0, 64)
            for index in range(max(count, len(state["inputs"]))):
                source = state["inputs"][index] if index < len(state["inputs"]) else None
                if node.input(index) != source:
                    node.setInput(index, source)
        except Exception:
            failures.append("topology")
        try:
            # An invalid position readback still permits an exact setter rollback.
            try:
                unchanged = _layout_position(node) == state["position"]
            except Exception:
                unchanged = False
            if not unchanged:
                node.setXYpos(*state["position"])
        except Exception:
            failures.append("position")
        try:
            if node.isSelected() is not state["selected"]:
                node.setSelected(state["selected"])
        except Exception:
            failures.append("selection")
    try:
        _verify_layout_preservation(before, _layout_snapshot(nuke), set())
    except Exception:
        failures.append("readback")
    root = nuke.root()
    try:
        root.setModified(True if failures else modified)
        if root.modified() is not (True if failures else modified):
            failures.append("modified")
    except Exception:
        failures.append("modified")
    if failures:
        raise NodeGraphMutationError("auto-layout rollback failed; script state could not be restored") from None


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
