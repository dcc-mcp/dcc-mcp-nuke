from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dcc_mcp_nuke.node_graph import connect_input, create_node, delete_node, get_knob, list_node_graph, set_knob

SKILL_ROOT = Path(__file__).parents[1] / "src" / "dcc_mcp_nuke" / "skills" / "nuke-node-graph"


class FakeKnob:
    def __init__(self, value, knob_class="Double_Knob"):
        self.current = value
        self.knob_class = knob_class

    def value(self):
        return self.current

    def setValue(self, value):
        self.current = value

    def Class(self):
        return self.knob_class


class FakeNode:
    def __init__(self, name, node_class, knobs=None, input_count=2):
        self._name = name
        self._class = node_class
        self._knobs = dict(knobs or {})
        self._inputs = [None] * input_count
        self.x = 0
        self.y = 0

    def name(self):
        return self._name

    def setName(self, name, **_kwargs):
        self._name = name

    def Class(self):
        return self._class

    def knobs(self):
        return self._knobs

    def __getitem__(self, name):
        return self._knobs[name]

    def inputs(self):
        return len(self._inputs)

    def maxInputs(self):
        return len(self._inputs)

    def input(self, index):
        return self._inputs[index]

    def setInput(self, index, node):
        self._inputs[index] = node

    def setXYpos(self, x, y):
        self.x, self.y = x, y


class FakeNuke:
    def __init__(self, nodes):
        self.nodes = list(nodes)
        self.deleted = []

    def allNodes(self, recurseGroups=True):
        assert recurseGroups is True
        return list(self.nodes)

    def allNodeClasses(self):
        return ["Blur", "Grade", "Merge2"]

    def toNode(self, name):
        return next((node for node in self.nodes if node.name() == name), None)

    def createNode(self, node_class, inpanel=False):
        assert inpanel is False
        node = FakeNode(f"{node_class}{len(self.nodes) + 1}", node_class)
        self.nodes.append(node)
        return node

    def delete(self, node):
        self.deleted.append(node)
        self.nodes.remove(node)


def test_node_graph_skill_declares_bounded_main_thread_crud():
    from dcc_mcp_core import validate_skill

    report = validate_skill(str(SKILL_ROOT))
    assert report.is_clean, [issue.message for issue in report.issues]

    manifest = yaml.safe_load((SKILL_ROOT / "tools.yaml").read_text(encoding="utf-8"))
    tools = {tool["name"]: tool for tool in manifest["tools"]}
    assert set(tools) == {"connect_input", "create_node", "delete_node", "get_knob", "set_knob"}
    assert all(tool["affinity"] == "main" for tool in tools.values())
    assert all(tool["input_schema"]["additionalProperties"] is False for tool in tools.values())
    assert tools["connect_input"]["input_schema"]["properties"]["input_index"]["maximum"] == 63


def test_list_node_graph_reports_topology_and_bounded_knob_values():
    read = FakeNode("Read1", "Read", {"file": FakeKnob("plate.exr"), "secret_script": FakeKnob("danger")})
    grade = FakeNode("Grade1", "Grade", {"multiply": FakeKnob([1.0, 0.8, 0.7, 1.0])})
    grade.setInput(0, read)

    result = list_node_graph(FakeNuke([read, grade]), max_nodes=10, max_knobs_per_node=8)

    assert result["nodes"][1]["inputs"] == [{"index": 0, "node": "Read1"}]
    assert result["nodes"][1]["knobs"]["multiply"] == [1.0, 0.8, 0.7, 1.0]
    assert "secret_script" not in result["nodes"][0]["knobs"]
    assert result["nodes"][0]["omitted_knobs"] == ["secret_script"]


def test_create_node_never_clears_existing_graph_and_reads_back_identity():
    existing = FakeNode("KeepMe", "Read")
    nuke = FakeNuke([existing])

    result = create_node(nuke, "Blur", name="SoftBlur", x=120, y=240)

    assert [node.name() for node in nuke.nodes] == ["KeepMe", "SoftBlur"]
    assert result == {"name": "SoftBlur", "class": "Blur", "x": 120, "y": 240}
    assert nuke.deleted == []


def test_create_node_rejects_unknown_class_before_mutation():
    nuke = FakeNuke([])

    with pytest.raises(ValueError, match="node_class is not available"):
        create_node(nuke, "NoSuchNode")

    assert nuke.nodes == []


def test_create_node_recovers_one_partial_mutation_when_host_raises():
    class MutateThenRaiseNuke(FakeNuke):
        def createNode(self, node_class, inpanel=False):
            super().createNode(node_class, inpanel=inpanel)
            raise OSError("host failed after mutation")

    nuke = MutateThenRaiseNuke([])

    with pytest.raises(OSError, match="host failed after mutation"):
        create_node(nuke, "Blur", name="SoftBlur")

    assert nuke.nodes == []


def test_create_node_preserves_ambiguous_partial_mutations_and_fails_closed():
    class AmbiguousMutateThenRaiseNuke(FakeNuke):
        def createNode(self, node_class, inpanel=False):
            super().createNode(node_class, inpanel=inpanel)
            self.nodes.append(FakeNode("ConcurrentBlur", "Blur"))
            raise OSError("host failed after concurrent mutation")

    nuke = AmbiguousMutateThenRaiseNuke([])

    with pytest.raises(RuntimeError, match="Blur partial creation attribution is ambiguous"):
        create_node(nuke, "Blur", name="SoftBlur")

    assert len(nuke.nodes) == 2


def test_delete_node_verifies_captured_identity_before_handle_invalidation():
    class InvalidatingNode(FakeNode):
        deleted = False

        def name(self):
            if self.deleted:
                raise RuntimeError("invalid node handle")
            return super().name()

    class InvalidatingNuke(FakeNuke):
        def delete(self, node):
            self.deleted.append(node)
            self.nodes.remove(node)
            node.deleted = True

    node = InvalidatingNode("Blur1", "Blur")
    nuke = InvalidatingNuke([node])

    result = delete_node(nuke, "Blur1")

    assert result == {"name": "Blur1", "class": "Blur", "deleted": True}
    assert nuke.nodes == []


def test_connect_input_and_static_knob_round_trip():
    source = FakeNode("Read1", "Read")
    target = FakeNode("Grade1", "Grade", {"multiply": FakeKnob(1.0)})
    nuke = FakeNuke([source, target])

    connection = connect_input(nuke, "Grade1", 0, "Read1")
    knob = set_knob(nuke, "Grade1", "multiply", 0.75)

    assert connection == {"node": "Grade1", "input_index": 0, "source": "Read1"}
    assert knob["value"] == 0.75
    assert get_knob(nuke, "Grade1", "multiply")["value"] == 0.75


def test_set_knob_rejects_executable_knob_classes():
    node = FakeNode("Danger", "NoOp", {"command": FakeKnob("", "PyScript_Knob")})

    with pytest.raises(ValueError, match="executable knob classes are not writable"):
        set_knob(FakeNuke([node]), "Danger", "command", "print('no')")
