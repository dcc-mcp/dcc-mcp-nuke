from pathlib import Path

import pytest

from dcc_mcp_nuke.compositing import build_layered_comp


class Knob:
    def __init__(self, value=None, choices=()):
        self.current = value
        self.choices = list(choices)

    def value(self):
        return self.current

    def setValue(self, value):
        self.current = value

    def values(self):
        return self.choices


class Node:
    def __init__(self, host, kind, name):
        self.host, self.kind, self._name = host, kind, name
        self.selected = False
        self.x = self.y = 0
        self.connections = [None] * 3
        self.fields = {
            name: Knob()
            for name in (
                "file",
                "first",
                "last",
                "origfirst",
                "origlast",
                "in",
                "multiply",
                "size",
                "operation",
                "type",
                "format",
                "resize",
                "file_type",
                "datatype",
            )
        }
        self.fields["colorspace"] = Knob("linear", ("linear", "sRGB"))

    def name(self):
        return self._name

    def fullName(self):
        return self._name

    def setName(self, name, **_kwargs):
        base = name
        index = 1
        while any(node is not self and node.name() == name for node in self.host.graph):
            index += 1
            name = base + str(index)
        self._name = name

    def Class(self):
        return self.kind

    def knobs(self):
        return self.fields

    def __getitem__(self, name):
        return self.fields[name]

    def isSelected(self):
        return self.selected

    def setSelected(self, selected):
        self.selected = selected

    def setXYpos(self, x, y):
        self.x, self.y = x, y

    def inputs(self):
        return len(self.connections)

    def input(self, index):
        return self.connections[index]

    def setInput(self, index, source):
        self.connections[index] = source
        return True

    def channels(self):
        return ["rgba.red", "rgba.green", "rgba.blue", "rgba.alpha"]


class Root(Node):
    def __init__(self, host):
        super().__init__(host, "Root", "Root")
        self.fields = {
            name: Knob(value)
            for name, value in {
                "first_frame": 10,
                "last_frame": 20,
                "fps": 24.0,
                "format": "HD_1080",
                "name": "original.nk",
            }.items()
        }
        self.dirty = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def modified(self):
        return self.dirty

    def setModified(self, modified):
        self.dirty = modified


class Host:
    def __init__(self):
        self.graph = []
        self.root_node = Root(self)
        self.deleted = []
        self.fail_class = None
        self.fail_save = False
        self.save_count = 0

    def root(self):
        return self.root_node

    def allNodes(self, recurseGroups=True):
        assert recurseGroups
        return list(self.graph)

    def toNode(self, name):
        return next((node for node in self.graph if node.name() == name), None)

    def createNode(self, kind, inpanel=False):
        assert not inpanel
        node = Node(self, kind, kind + str(len(self.graph)))
        selected = next((node for node in self.graph if node.selected), None)
        node.connections[0] = selected
        for previous in self.graph:
            previous.selected = False
        node.selected = True
        self.graph.append(node)
        if kind == self.fail_class:
            raise RuntimeError("node callback failed")
        return node

    def delete(self, node):
        self.deleted.append(node)
        self.graph.remove(node)

    def addFormat(self, value):
        return value

    def scriptSaveAs(self, path, overwrite):
        assert overwrite == 1
        self.root_node["name"].setValue(path)
        if self.fail_save:
            raise RuntimeError("save failed")
        Path(path).write_text("\n".join(node.name() for node in self.graph), encoding="utf-8")
        self.save_count += 1
        self.root_node.dirty = False


def _manifest(tmp_path):
    return {
        "script_path": str(tmp_path / "comp.nk"),
        "output_path": str(tmp_path / "comp.png"),
        "first_frame": 1,
        "last_frame": 5,
        "output_colorspace": "sRGB",
        "layers": [{"name": "Beauty", "path": str(tmp_path / "plate.exr"), "colorspace": "linear"}],
    }


def _existing(host):
    read = Node(host, "Read", "Beauty")
    grade = Node(host, "Grade", "DCC_MCP_FINAL_WRITE")
    grade.setInput(0, read)
    grade["multiply"].setValue(0.75)
    grade.selected = True
    host.graph.extend((read, grade))
    return read, grade


def _assert_preserved(host, read, grade):
    assert read in host.graph and grade in host.graph
    assert read.name() == "Beauty" and grade.name() == "DCC_MCP_FINAL_WRITE"
    assert grade.input(0) is read and grade["multiply"].value() == 0.75
    assert grade.selected and not read.selected
    assert read not in host.deleted and grade not in host.deleted


def test_build_is_additive_handles_name_collisions_and_repeated_builds(tmp_path):
    host = Host()
    read, grade = _existing(host)
    first = build_layered_comp(host, _manifest(tmp_path))
    _assert_preserved(host, read, grade)
    assert first["read_nodes"] == ["Beauty2"]
    assert first["write_node"] == "DCC_MCP_FINAL_WRITE2"
    assert host.toNode(first["write_node"])["colorspace"].value() == "sRGB"
    assert not any(node.selected for node in host.graph if node not in (read, grade))
    count = len(host.graph)
    second = build_layered_comp(host, _manifest(tmp_path))
    assert len(host.graph) > count and host.save_count == 2
    assert second["write_node"] != first["write_node"]
    _assert_preserved(host, read, grade)


@pytest.mark.parametrize("failure", ["colorspace", "partial_create", "save", "missing_layer"])
def test_failed_build_removes_only_added_nodes_and_restores_root(tmp_path, failure):
    host = Host()
    read, grade = _existing(host)
    before_root = {name: knob.value() for name, knob in host.root().knobs().items()}
    manifest = _manifest(tmp_path)
    if failure == "colorspace":
        manifest["layers"][0]["colorspace"] = "not-enumerated"
    elif failure == "partial_create":
        host.fail_class = "Reformat"
    elif failure == "save":
        host.fail_save = True
    else:
        manifest["required_layers"] = ["missing"]
    with pytest.raises((ValueError, RuntimeError)):
        build_layered_comp(host, manifest)
    assert host.graph == [read, grade]
    _assert_preserved(host, read, grade)
    assert {name: knob.value() for name, knob in host.root().knobs().items()} == before_root
    assert host.root().dirty
    assert not (tmp_path / "comp.nk").exists()


def test_rejects_invalid_write_colorspace_and_preserves_clean_root(tmp_path):
    host = Host()
    read, grade = _existing(host)
    host.root_node.dirty = False
    manifest = _manifest(tmp_path)
    manifest["output_colorspace"] = "not-enumerated"
    with pytest.raises(ValueError, match="enumerated"):
        build_layered_comp(host, manifest)
    assert host.graph == [read, grade] and not host.root().dirty
    _assert_preserved(host, read, grade)


def test_failure_never_deletes_preexisting_node_renamed_by_host_callback(tmp_path, monkeypatch):
    host = Host()
    read, grade = _existing(host)
    host.root_node.dirty = False
    original_create = host.createNode

    def create(kind, **kwargs):
        node = original_create(kind, **kwargs)
        if kind == "Reformat":
            read.setName("Callback_Renamed_Beauty")
        return node

    monkeypatch.setattr(host, "createNode", create)
    with pytest.raises(RuntimeError, match="pre-existing graph changed"):
        build_layered_comp(host, _manifest(tmp_path))
    assert host.graph == [read, grade]
    assert read not in host.deleted and grade not in host.deleted
    assert host.root().dirty
