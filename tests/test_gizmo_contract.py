import hashlib
import json
from pathlib import Path

import pytest

from dcc_mcp_nuke import gizmos


class Knob:
    def __init__(self, name, value=None, knob_class="Double_Knob"):
        self._name = name
        self._value = value
        self._class = knob_class
        self.link = None

    def name(self):
        return self._name

    def Class(self):
        return self._class

    def value(self):
        return self._value

    def setValue(self, value):
        self._value = value

    def setRange(self, minimum, maximum):
        self.range = (minimum, maximum)

    def setFlag(self, _flag):
        pass

    def makeLink(self, node, knob):
        self.link = f"{node}.{knob}"


class Node:
    def __init__(self, name, node_class="Grade", children=()):
        self._name = name
        self._class = node_class
        self._children = list(children)
        self._knobs = {
            "selected": Knob("selected", False, "Boolean_Knob"),
            "label": Knob("label", "", "String_Knob"),
            "multiply": Knob("multiply", 1.0),
        }
        self.inputs = {}

    def name(self):
        return self._name

    def setName(self, name):
        self._name = name

    def Class(self):
        return self._class

    def nodes(self):
        return self._children

    def knob(self, name):
        return self._knobs.get(name)

    def knobs(self):
        return self._knobs

    def addKnob(self, knob):
        self._knobs[knob.name()] = knob

    def removeKnob(self, knob):
        self._knobs.pop(knob.name(), None)

    def setSelected(self, selected):
        self._knobs["selected"].setValue(selected)

    def setInput(self, index, node):
        self.inputs[index] = node

    def setXYpos(self, x, y):
        self.position = (x, y)


class FakeNuke:
    INVISIBLE = 0x400

    def __init__(self, group, serialized="Group {\n name DccAtmosphereGlow\n}\n"):
        self.group = group
        self.serialized = serialized
        self.created = []
        self.deleted = []
        self.nodes_by_name = {group.name(): group}
        self.plugin_paths = []

    def toNode(self, name):
        return self.nodes_by_name.get(name)

    def allNodes(self, recurseGroups=False):
        return list(self.nodes_by_name.values())

    def Link_Knob(self, name, _label):
        return Knob(name)

    def String_Knob(self, name, _label):
        return Knob(name, knob_class="String_Knob")

    def nodeCopy(self, path):
        Path(path).write_text(self.serialized, encoding="utf-8")

    def pluginAddPath(self, path):
        self.plugin_paths.append(path)

    def load(self, _name):
        return True

    def createNode(self, node_class, inpanel=False):
        node = Node(f"{node_class}1", node_class, self.group.nodes())
        manifest = Knob("dcc_mcp_asset_manifest", self.manifest_json, "String_Knob")
        node.addKnob(manifest)
        for public_knob in json.loads(self.manifest_json)["exposed_knobs"]:
            node.addKnob(Knob(public_knob["name"], public_knob["default"]))
        self.created.append(node)
        self.nodes_by_name[node.name()] = node
        return node

    def delete(self, node):
        self.deleted.append(node)


def contract():
    return {
        "gizmo_id": "dcc.atmosphere_glow",
        "version": "1.0.0",
        "display_name": "Atmosphere Glow",
        "exposed_knobs": [
            {
                "name": "rim_gain",
                "target_node": "Grade1",
                "target_knob": "multiply",
                "type": "float",
                "default": 1.0,
                "minimum": 0.0,
                "maximum": 5.0,
            }
        ],
        "input_contract": [{"name": "beauty", "required": True}],
    }


def test_create_is_root_confined_versioned_and_deterministic(tmp_path, monkeypatch):
    monkeypatch.setenv("DCC_MCP_NUKE_PLUGIN_ROOT", str(tmp_path))
    grade = Node("Grade1")
    grade._knobs["multiply"] = Knob("multiply", 1.0, "WH_Knob")
    group = Node("AtmosphereGroup", "Group", [Node("Input1", "Input"), grade])
    nuke = FakeNuke(group)

    first = gizmos.create_from_group(
        nuke, group_node="AtmosphereGroup", conflict_policy="write_versioned", **contract()
    )
    second = gizmos.create_from_group(
        nuke, group_node="AtmosphereGroup", conflict_policy="replace_same_version", **contract()
    )

    target = Path(first["gizmo_path"])
    assert target.is_relative_to(tmp_path.resolve())
    assert target.parts[-3:-1] == ("dcc.atmosphere_glow", "1.0.0")
    assert first["sha256"] == second["sha256"]
    assert json.loads(Path(first["manifest_path"]).read_text())["gizmo_id"] == "dcc.atmosphere_glow"
    assert group.name() == "AtmosphereGroup"


def test_create_rejects_callbacks_before_publishing(tmp_path, monkeypatch):
    monkeypatch.setenv("DCC_MCP_NUKE_PLUGIN_ROOT", str(tmp_path))
    group = Node("Unsafe", "Group", [Node("Input1", "Input"), Node("Grade1")])
    nuke = FakeNuke(group, "Group {\n knobChanged {python {danger()}}\n}\n")

    with pytest.raises(ValueError, match="forbidden callback"):
        gizmos.create_from_group(nuke, group_node="Unsafe", conflict_policy="write_versioned", **contract())

    assert not list(tmp_path.rglob("*.gizmo"))


@pytest.mark.parametrize("conflict_policy", ["fail", "write_versioned"])
def test_create_race_refuses_overwrite_and_preserves_existing_bytes(tmp_path, monkeypatch, conflict_policy):
    monkeypatch.setenv("DCC_MCP_NUKE_PLUGIN_ROOT", str(tmp_path))
    grade = Node("Grade1")
    grade._knobs["multiply"] = Knob("multiply", 1.0, "WH_Knob")
    group = Node("AtmosphereGroup", "Group", [Node("Input1", "Input"), grade])
    nuke = FakeNuke(group)
    target = tmp_path / "dcc.atmosphere_glow" / "1.0.0" / f"{gizmos._class_name('dcc.atmosphere_glow')}.gizmo"
    original = b"existing gizmo\n"
    original_node_copy = nuke.nodeCopy

    def node_copy(path):
        original_node_copy(path)
        target.write_bytes(original)

    nuke.nodeCopy = node_copy
    before = hashlib.sha256(original).hexdigest()

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        gizmos.create_from_group(nuke, group_node="AtmosphereGroup", conflict_policy=conflict_policy, **contract())

    assert hashlib.sha256(target.read_bytes()).hexdigest() == before
    assert not list(target.parent.glob("*.dcc-mcp.tmp"))
    assert group.name() == "AtmosphereGroup"


def test_instantiate_is_idempotent_and_range_checked(tmp_path, monkeypatch):
    monkeypatch.setenv("DCC_MCP_NUKE_PLUGIN_ROOT", str(tmp_path))
    grade = Node("Grade1")
    group = Node("AtmosphereGroup", "Group", [Node("Input1", "Input"), grade])
    nuke = FakeNuke(group)
    created = gizmos.create_from_group(
        nuke, group_node="AtmosphereGroup", conflict_policy="write_versioned", **contract()
    )
    nuke.manifest_json = Path(created["manifest_path"]).read_text()
    beauty = Node("Beauty", "Read")
    nuke.nodes_by_name[beauty.name()] = beauty

    first = gizmos.instantiate(
        nuke,
        gizmo_id="dcc.atmosphere_glow",
        version="1.0.0",
        node_name="AtmosphereGlow1",
        input_nodes={"beauty": "Beauty"},
        knob_overrides={"rim_gain": 1.5},
        xpos=120,
        ypos=240,
    )
    nuke.nodes_by_name[first["node_name"]] = nuke.created[-1]
    second = gizmos.instantiate(
        nuke,
        gizmo_id="dcc.atmosphere_glow",
        version="1.0.0",
        node_name="AtmosphereGlow1",
        input_nodes={"beauty": "Beauty"},
        knob_overrides={"rim_gain": 1.5},
    )

    assert first["reused"] is False
    assert second["reused"] is True
    assert nuke.created[-1].inputs == {0: beauty}
    with pytest.raises(ValueError, match="rim_gain.*maximum"):
        gizmos.instantiate(
            nuke,
            gizmo_id="dcc.atmosphere_glow",
            version="1.0.0",
            input_nodes={"beauty": "Beauty"},
            knob_overrides={"rim_gain": 7.0},
        )


def test_validate_reports_hash_interface_dependencies_and_forbidden_code(tmp_path, monkeypatch):
    monkeypatch.setenv("DCC_MCP_NUKE_PLUGIN_ROOT", str(tmp_path))
    group = Node("AtmosphereGroup", "Group", [Node("Input1", "Input"), Node("Grade1")])
    nuke = FakeNuke(group)
    created = gizmos.create_from_group(
        nuke, group_node="AtmosphereGroup", conflict_policy="write_versioned", **contract()
    )
    nuke.manifest_json = Path(created["manifest_path"]).read_text()

    report = gizmos.validate(nuke, "dcc.atmosphere_glow", "1.0.0")

    assert report["valid"] is True
    assert report["sha256"] == created["sha256"]
    assert report["schema_version"] == 1
    assert report["input_contract"] == [{"name": "beauty", "required": True}]
    assert report["forbidden_code"] == []
    assert report["clean_load"]["passed"] is True
    assert report["internal_node_count"] == 2
