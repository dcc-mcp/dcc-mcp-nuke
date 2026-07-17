import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

SCRIPTS = Path(__file__).parents[1] / "src" / "dcc_mcp_nuke" / "skills" / "nuke-node-assets" / "scripts"


def _load_script(name):
    path = SCRIPTS / name
    spec = importlib.util.spec_from_file_location(f"node_assets_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Knob:
    def __init__(self, name, label=""):
        self._name = name
        self._label = label
        self._value = None
        self.link = None

    def name(self):
        return self._name

    def label(self):
        return self._label

    def setFlag(self, _flag):
        return None

    def setLink(self, target):
        self.link = target

    def makeLink(self, node, knob):
        self.link = f"{node}.{knob}"

    def getLink(self):
        return self.link

    def setValue(self, value):
        self._value = value

    def value(self):
        return self._value


class _Node:
    def __init__(self, name, node_class="Grade", children=None):
        self._name = name
        self._class = node_class
        self._children = list(children or [])
        self._knobs = {"selected": _Knob("selected"), "multiply": _Knob("multiply")}

    def name(self):
        return self._name

    def setName(self, name):
        self._name = name

    def Class(self):
        return self._class

    def setSelected(self, selected):
        self._knobs["selected"].setValue(selected)

    def knob(self, name):
        return self._knobs.get(name)

    def knobs(self):
        return self._knobs

    def addKnob(self, knob):
        self._knobs[knob.name()] = knob

    def nodes(self):
        return self._children

    def hasError(self):
        return False


def test_package_gizmo_groups_nodes_exposes_knobs_and_writes_versioned_asset(tmp_path, monkeypatch):
    module = _load_script("package_gizmo.py")
    grade = _Node("Grade1")
    blur = _Node("Blur1", "Blur")
    group = _Node("Group1", "Group", [grade, blur])
    nodes = {node.name(): node for node in (grade, blur)}

    nuke = ModuleType("nuke")
    nuke.INVISIBLE = 0x400
    nuke.allNodes = MagicMock(return_value=[grade, blur])
    nuke.toNode = MagicMock(side_effect=nodes.get)
    nuke.collapseToGroup = MagicMock(return_value=group)
    nuke.Link_Knob = _Knob
    nuke.String_Knob = _Knob

    def node_copy(path):
        Path(path).write_text("set cut_paste_input [stack 0]\nGroup {\n name SolarBloom\n}\n", encoding="utf-8")
        return True

    nuke.nodeCopy = MagicMock(side_effect=node_copy)
    monkeypatch.setitem(sys.modules, "nuke", nuke)

    target = tmp_path / "SolarBloom.gizmo"
    result = module.main.__wrapped__(
        node_names=["Grade1", "Blur1"],
        gizmo_name="SolarBloom",
        output_path=str(target),
        version="1.2.0",
        exposed_knobs=[
            {
                "name": "bloom_gain",
                "label": "Bloom Gain",
                "target_node": "Grade1",
                "target_knob": "multiply",
            }
        ],
    )

    assert result["success"] is True
    assert result["context"] == {
        "gizmo_name": "SolarBloom",
        "group_node": "SolarBloom",
        "output_path": str(target.resolve()),
        "version": "1.2.0",
        "node_count": 2,
        "exposed_knobs": ["bloom_gain"],
    }
    assert "Gizmo {" in target.read_text(encoding="utf-8")
    assert "\nGroup {" not in target.read_text(encoding="utf-8")
    assert group.knob("bloom_gain").link == "Grade1.multiply"
    assert json.loads(group.knob("dcc_mcp_asset_manifest").value()) == {
        "name": "SolarBloom",
        "version": "1.2.0",
        "exposed_knobs": ["bloom_gain"],
    }
    nuke.collapseToGroup.assert_called_once_with(show=False)


def test_instantiate_gizmo_loads_asset_and_applies_exposed_knobs(tmp_path, monkeypatch):
    module = _load_script("instantiate_gizmo.py")
    target = tmp_path / "SolarBloom.gizmo"
    target.write_text("Gizmo {\n name SolarBloom\n}\n", encoding="utf-8")
    instance = _Node("SolarBloom1", "SolarBloom")
    manifest = _Knob("dcc_mcp_asset_manifest")
    manifest.setValue(json.dumps({"name": "SolarBloom", "version": "1.2.0", "exposed_knobs": ["bloom_gain"]}))
    instance.addKnob(manifest)
    instance.addKnob(_Knob("bloom_gain", "Bloom Gain"))

    nuke = ModuleType("nuke")
    nuke.pluginAddPath = MagicMock()
    nuke.load = MagicMock()
    nuke.createNode = MagicMock(return_value=instance)
    nuke.delete = MagicMock()
    monkeypatch.setitem(sys.modules, "nuke", nuke)

    result = module.main.__wrapped__(
        gizmo_path=str(target),
        node_name="ShotBloom",
        knob_values={"bloom_gain": 1.4},
    )

    assert result["success"] is True
    assert result["context"] == {
        "gizmo_path": str(target.resolve()),
        "asset_name": "SolarBloom",
        "node_name": "ShotBloom",
        "node_class": "SolarBloom",
        "version": "1.2.0",
        "applied_knobs": ["bloom_gain"],
    }
    nuke.pluginAddPath.assert_called_once_with(str(tmp_path.resolve()))
    nuke.load.assert_called_once_with("SolarBloom")
    nuke.createNode.assert_called_once_with("SolarBloom", inpanel=False)
    assert instance.knob("bloom_gain").value() == 1.4
    nuke.delete.assert_not_called()


def test_inspect_gizmo_reports_manifest_interface_and_validation(monkeypatch):
    module = _load_script("inspect_gizmo.py")
    instance = _Node("ShotBloom", "SolarBloom", [_Node("Grade1"), _Node("Blur1", "Blur")])
    manifest = _Knob("dcc_mcp_asset_manifest")
    manifest.setValue(json.dumps({"name": "SolarBloom", "version": "1.2.0", "exposed_knobs": ["bloom_gain"]}))
    gain = _Knob("bloom_gain", "Bloom Gain")
    gain.setLink("Grade1.multiply")
    instance.addKnob(manifest)
    instance.addKnob(gain)

    nuke = ModuleType("nuke")
    nuke.toNode = MagicMock(return_value=instance)
    monkeypatch.setitem(sys.modules, "nuke", nuke)

    result = module.main.__wrapped__(node_name="ShotBloom")

    assert result["success"] is True
    assert result["context"] == {
        "node_name": "ShotBloom",
        "node_class": "SolarBloom",
        "asset_name": "SolarBloom",
        "version": "1.2.0",
        "valid": True,
        "issues": [],
        "child_nodes": [
            {"name": "Grade1", "class": "Grade"},
            {"name": "Blur1", "class": "Blur"},
        ],
        "exposed_knobs": [{"name": "bloom_gain", "label": "Bloom Gain", "link": "Grade1.multiply"}],
    }
