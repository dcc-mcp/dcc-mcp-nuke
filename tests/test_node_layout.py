from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from dcc_mcp_nuke.node_graph import NodeGraphMutationError, auto_layout_nodes, list_node_graph


class Node:
    def __init__(self, host, name, *, parent=None, kind="Blur", x=0, y=0):
        self.host, self._name, self._parent, self.kind = host, name, parent or host.root(), kind
        self.x, self.y, self.selected, self.is_locked = x, y, False, False
        self.links = []
        self.action = None
        host.nodes.append(self)

    def name(self):
        return self._name

    def fullName(self):
        prefix = "" if self._parent == self.host.root() else self._parent.fullName() + "."
        return prefix + self._name

    def Class(self):
        return self.kind

    def parent(self):
        return self._parent

    def xpos(self):
        return self.x

    def ypos(self):
        return self.y

    def setXYpos(self, x, y):
        self.x, self.y = x, y
        self.host.root().dirty = True

    def isSelected(self):
        return self.selected

    def setSelected(self, selected):
        self.selected = selected

    def locked(self):
        return self.is_locked

    def inputs(self):
        return max((index + 1 for index, source in enumerate(self.links) if source is not None), default=0)

    def input(self, index):
        return self.links[index] if index < len(self.links) else None

    def setInput(self, index, source):
        self.links.extend([None] * max(0, index + 1 - len(self.links)))
        self.links[index] = source

    def knobs(self):
        return {}

    def autoplace(self):
        assert self.host.context == self.parent()
        self.host.calls.append(self.fullName())
        self.setXYpos(self.x + 120, self.y + 90)
        if self.action:
            self.action()

    def __enter__(self):
        self.host.context_stack.append(self.host.context)
        self.host.context = self
        return self

    def __exit__(self, *_args):
        self.host.context = self.host.context_stack.pop()


class Root(Node):
    def __init__(self, host):
        self.host, self.dirty, self.is_locked = host, False, False

    def modified(self):
        return self.dirty

    def setModified(self, modified):
        self.dirty = modified


class Nuke:
    def __init__(self):
        self.nodes, self.calls, self.context_stack = [], [], []
        self._root = Root(self)
        self.context = self._root

    def root(self):
        return self._root

    def allNodes(self, recurseGroups=True):
        assert self.context == self.root()
        assert recurseGroups is True
        return list(self.nodes)

    def toNode(self, name):
        assert self.context == self.root()
        return next((node for node in self.nodes if node.fullName() == name), None)


def graph_state(host):
    return [
        (node.fullName(), node.x, node.y, node.selected, tuple(node.input(index) for index in range(node.inputs())))
        for node in host.nodes
    ]


def test_native_layout_changes_only_requested_positions_and_preserves_context():
    host = Nuke()
    source = Node(host, "Read1", kind="Read", x=10, y=20)
    grade = Node(host, "Grade1", kind="Grade", x=10, y=20)
    merge = Node(host, "Merge1", kind="Merge2", x=10, y=20)
    outside = Node(host, "Keep1", x=800, y=900)
    disconnected = Node(host, "Loose1", x=10, y=20)
    grade.setInput(0, source)
    merge.setInput(0, source)
    merge.setInput(1, grade)
    grade.selected = True
    other_context = Node(host, "OtherGroup", kind="Group")
    host.context = other_context
    before = graph_state(host)
    grade.action = lambda: setattr(outside, "selected", True)

    result = auto_layout_nodes(host, ["Merge1", "Grade1", "Loose1"])

    assert host.context == other_context
    assert host.calls == ["Merge1", "Grade1", "Loose1"]
    assert result["algorithm"] == "nuke_native_autoplace"
    assert result["parent"] == "root"
    assert result["node_count"] == result["changed_count"] == 3
    assert result["nodes"][0] == {
        "name": "Merge1",
        "full_name": "Merge1",
        "before": {"x": 10, "y": 20},
        "x": 130,
        "y": 110,
    }
    assert (source.x, source.y) == (10, 20)
    assert (outside.x, outside.y) == (800, 900)
    assert grade.selected is True and outside.selected is False
    assert merge.input(0) is source and merge.input(1) is grade
    assert len(graph_state(host)) == len(before)
    assert disconnected.x == 130
    assert host.root().modified() is True


def test_group_scope_uses_full_names_and_preserves_group_contents():
    host = Nuke()
    group = Node(host, "Group1", kind="Group")
    nested = Node(host, "Blur1", parent=group)
    sibling = Node(host, "Blur1", x=500, y=600)

    result = auto_layout_nodes(host, ["Group1.Blur1"])

    assert result["parent"] == "Group1"
    assert (nested.x, nested.y) == (120, 90)
    assert (group.x, group.y) == (0, 0)
    assert (sibling.x, sibling.y) == (500, 600)
    auto_layout_nodes(host, ["Group1"])
    assert (group.x, group.y) == (120, 90)
    assert (nested.x, nested.y) == (120, 90)


def test_graph_listing_exposes_exact_full_names_and_positions_without_knobs():
    host = Nuke()
    group = Node(host, "Group1", kind="Group")
    Node(host, "Blur1", parent=group, x=125, y=-50)

    result = list_node_graph(host, max_knobs_per_node=0)

    assert result["nodes"][1]["full_name"] == "Group1.Blur1"
    assert (result["nodes"][1]["x"], result["nodes"][1]["y"]) == (125, -50)


@pytest.mark.parametrize("names", [None, "Blur1", [], ["Blur1"] * 257, ["Blur1", "Blur1"], ["bad[name"], [123]])
def test_invalid_request_is_rejected_before_mutation(names):
    host = Nuke()
    Node(host, "Blur1")
    before = graph_state(host)

    with pytest.raises(ValueError):
        auto_layout_nodes(host, names)

    assert host.calls == [] and graph_state(host) == before
    assert host.root().modified() is False


@pytest.mark.parametrize(
    "case", ["missing", "ambiguous", "mixed", "backdrop", "locked", "parent_locked", "root_locked"]
)
def test_unsupported_scope_is_rejected_before_mutation(case):
    host = Nuke()
    group = Node(host, "Group1", kind="Group")
    target = Node(host, "Blur1", parent=group)
    names = ["Group1.Blur1"]
    if case == "missing":
        names = ["Blur1"]  # A short name cannot alias a nested target.
    elif case == "ambiguous":
        Node(host, "Blur1", parent=group)
    elif case == "mixed":
        Node(host, "Other1")
        names.append("Other1")
    elif case == "backdrop":
        target.kind = "BackdropNode"
    else:
        {"locked": target, "parent_locked": group, "root_locked": host.root()}[case].is_locked = True
    before = graph_state(host)

    with pytest.raises((ValueError, NodeGraphMutationError)):
        auto_layout_nodes(host, names)

    assert host.calls == [] and graph_state(host) == before


@pytest.mark.parametrize("coordinate", [True, 1.5, float("nan"), float("inf"), 1_000_001])
def test_invalid_native_coordinate_fails_before_mutation(coordinate):
    host = Nuke()
    node = Node(host, "Blur1", x=coordinate)

    with pytest.raises(ValueError, match="coordinate"):
        auto_layout_nodes(host, ["Blur1"])

    assert host.calls == [] and node.x is coordinate


@pytest.mark.parametrize("method", ["xpos", "ypos", "setXYpos", "autoplace", "locked", "setSelected", "parent"])
def test_missing_required_native_api_fails_before_mutation(method):
    host = Nuke()
    node = Node(host, "Blur1")
    setattr(node, method, None)

    with pytest.raises(NodeGraphMutationError):
        auto_layout_nodes(host, ["Blur1"])

    assert host.calls == []


@pytest.mark.parametrize("modified", [False, True])
def test_native_failure_rolls_back_all_positions_selection_and_dirty_state(modified):
    host = Nuke()
    first, second = Node(host, "Blur1", x=4, y=5), Node(host, "Blur2", x=6, y=7)
    first.selected = True
    host.root().dirty = modified
    before = graph_state(host)

    def fail_after_mutation():
        second.selected = True
        raise OSError("private host path must not escape")

    second.action = fail_after_mutation
    with pytest.raises(NodeGraphMutationError, match="original graph layout restored"):
        auto_layout_nodes(host, ["Blur1", "Blur2"])

    assert graph_state(host) == before
    assert host.root().modified() is modified


@pytest.mark.parametrize("change", ["outside_position", "topology", "invalid_readback"])
def test_unexpected_native_changes_fail_closed_and_restore_graph(change):
    host = Nuke()
    target, outside = Node(host, "Blur1"), Node(host, "Keep1", x=700, y=800)
    outside.setInput(0, target)
    before = graph_state(host)

    def mutate():
        if change == "outside_position":
            outside.setXYpos(1, 2)
        elif change == "topology":
            outside.setInput(0, None)
            target.setInput(5, outside)
        else:
            target.x = float("nan")

    target.action = mutate
    with pytest.raises(NodeGraphMutationError, match="original graph layout restored"):
        auto_layout_nodes(host, ["Blur1"])

    assert graph_state(host) == before
    assert target.inputs() == 0
    assert host.root().modified() is False


def test_rollback_failure_is_explicit_and_leaves_script_dirty():
    host = Nuke()
    target = Node(host, "Blur1")

    def fail_rollback():
        target.setXYpos = lambda *_args: (_ for _ in ()).throw(RuntimeError("setter failed"))
        raise RuntimeError("native failure")

    target.action = fail_rollback
    with pytest.raises(NodeGraphMutationError, match="rollback failed"):
        auto_layout_nodes(host, ["Blur1"])

    assert host.root().modified() is True


def test_unattributable_new_nodes_are_preserved_and_rollback_failure_is_reported():
    host = Nuke()
    target = Node(host, "Blur1")
    target.action = lambda: Node(host, "Concurrent1")

    with pytest.raises(NodeGraphMutationError, match="rollback failed"):
        auto_layout_nodes(host, ["Blur1"])

    assert [node.name() for node in host.nodes] == ["Blur1", "Concurrent1"]
    assert (target.x, target.y) == (0, 0)
    assert host.root().modified() is True


def test_native_noop_preserves_clean_script_state():
    host = Nuke()
    node = Node(host, "Blur1")
    node.autoplace = lambda: host.root().setModified(True)

    result = auto_layout_nodes(host, ["Blur1"])

    assert result["changed_count"] == 0
    assert host.root().modified() is False


@pytest.mark.parametrize("value", [0, None, "false"])
def test_unobservable_lock_or_selection_state_fails_before_mutation(value):
    host = Nuke()
    target = Node(host, "Blur1")
    target.locked = lambda: value
    with pytest.raises(NodeGraphMutationError, match="lock state"):
        auto_layout_nodes(host, ["Blur1"])
    target.locked = lambda: False
    target.selected = value
    with pytest.raises(NodeGraphMutationError, match="selection state"):
        auto_layout_nodes(host, ["Blur1"])
    assert host.calls == []


def test_graph_and_input_slot_caps_fail_before_mutation():
    host = Nuke()
    target = Node(host, "Blur1")
    target.setInput(64, target)
    with pytest.raises(ValueError, match="input count"):
        auto_layout_nodes(host, ["Blur1"])
    target.links = []
    for index in range(1000):
        Node(host, f"Other{index}")
    with pytest.raises(ValueError, match="1000 nodes"):
        auto_layout_nodes(host, ["Blur1"])
    assert host.calls == []


def test_skill_entrypoint_returns_layout_readback_and_safe_errors(monkeypatch):
    path = Path(__file__).parents[1] / "src/dcc_mcp_nuke/skills/nuke-node-graph/scripts/auto_layout_nodes.py"
    spec = importlib.util.spec_from_file_location("auto_layout_nodes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    host = Nuke()
    Node(host, "Blur1")
    monkeypatch.setitem(sys.modules, "nuke", host)

    result = module.main.__wrapped__(node_names=["Blur1"])
    assert result["context"]["nodes"][0]["x"] == 120
    failure = module.main.__wrapped__(node_names=["NoSuchNode"])
    assert failure["success"] is False
