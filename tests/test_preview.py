import struct
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dcc_mcp_nuke import preview


class Node:
    def __init__(self, host, kind, parent=None, name=None, inputs=(), **knobs):
        self.host, self.kind, self._parent = host, kind, parent
        self._name = name or kind + str(len(host.graph))
        self.selected = False
        self.input_nodes = list(inputs)
        self.values = knobs
        self.children = []
        host.graph.append(self)
        if parent:
            parent.children.append(self)

    def __getitem__(self, name):
        return SimpleNamespace(setValue=lambda value: self.values.update({name: value}))

    def __enter__(self):
        self.previous = self.host.context
        self.host.context = self
        return self

    def __exit__(self, *_args):
        self.host.context = self.previous

    def name(self):
        return self._name

    def fullName(self):
        return self._name if self._parent is self.host.root_node else self._parent.fullName() + "." + self._name

    def parent(self):
        return self._parent

    def isSelected(self):
        return self.selected

    def setSelected(self, value):
        self.selected = value

    def setInput(self, index, node):
        while len(self.input_nodes) <= index:
            self.input_nodes.append(None)
        self.input_nodes[index] = node
        return True

    def input(self, index):
        return self.input_nodes[index]

    def Class(self):
        return self.kind

    def channels(self):
        return ["rgba.red", "rgba.green", "rgba.blue", "rgba.alpha", "depth.Z"]

    def width(self):
        return self.host.width

    def height(self):
        return self.host.height

    def pixelAspect(self):
        return self.host.aspect

    def bbox(self):
        return [-2, -3, 1924, 1086]

    def sample(self, _channel, _x, _y, _dx, _dy, frame):
        assert frame == self.host.current_frame
        return 0.25


def _png(width, height):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + b"\x40\x40\x40" * width) * height))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def host(monkeypatch):
    nuke = SimpleNamespace(graph=[], context=None, current_frame=12, width=1920, height=1080, aspect=1.0)
    root = Node(nuke, "Root", name="root")
    nuke.root_node = root
    nuke.context = root
    root.dirty, root.proxy_mode = False, True
    root.proxy = lambda: root.proxy_mode
    root.modified = lambda: root.dirty
    root.setProxy = lambda value: setattr(root, "proxy_mode", value)
    root.setModified = lambda value: setattr(root, "dirty", value)
    nuke.root = lambda: root
    nuke.allNodes = lambda **_kwargs: [node for node in nuke.graph if node is not root]
    nuke.toNode = lambda name: next((node for node in nuke.graph if node is not root and node.fullName() == name), None)
    nuke.Undo = SimpleNamespace(disable=MagicMock(), enable=MagicMock())

    def frame(value=None):
        if value is not None:
            nuke.current_frame = value
        return nuke.current_frame

    nuke.frame = frame

    def create(kind, **kwargs):
        root.dirty = True
        for node in nuke.graph:
            node.selected = False
        return Node(nuke, kind, parent=nuke.context, **kwargs)

    nuke.nodes = SimpleNamespace(
        **{
            kind: (lambda kind=kind, **kwargs: create(kind, **kwargs))
            for kind in ("Group", "Input", "Copy", "Reformat", "Write", "OCIODisplay")
        }
    )

    def delete(node):
        for child in list(node.children):
            delete(child)
        nuke.graph.remove(node)
        node.parent().children.remove(node)

    nuke.delete = delete

    def execute(write, first, last, increment):
        assert first == last == nuke.current_frame and increment == 1
        assert write.values["raw"] is True
        reformat = next(node for node in nuke.graph if node.kind == "Reformat")
        Path(write.values["file"]).write_bytes(_png(reformat.values["box_width"], reformat.values["box_height"]))

    nuke.execute = execute
    nuke.source = Node(nuke, "Read", parent=root, name="Beauty")
    nuke.source.selected = True
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    yield nuke
    preview._cleanup_all()


def test_preview_is_bounded_referenced_and_restores_script(host):
    result = preview.render_node_preview(host, node_name="Beauty", frame=34)

    path = Path(result["file_path"])
    assert path.is_file() and path.parent.name.startswith("dcc-mcp-nuke-preview-")
    assert result["file_ref"]["uri"] == path.as_uri()
    assert (result["file_ref"]["width"], result["file_ref"]["height"]) == (512, 288)
    assert result["file_ref"]["digest"].startswith("sha256:")
    assert result["source_bbox"] == {"x": -2, "y": -3, "width": 1924, "height": 1086}
    assert len(result["statistics"]) == 4
    assert result["sampling"]["samples_per_channel"] <= 256
    assert host.graph == [host.root_node, host.source]
    assert host.source.selected and host.current_frame == 12 and host.root_node.proxy_mode
    assert not host.root_node.dirty
    assert host.context is host.root_node
    host.Undo.disable.assert_called_once()
    host.Undo.enable.assert_called_once()
    assert preview.cleanup_preview(result["preview_id"])["removed"] is True
    assert not path.parent.exists()


def test_single_channel_grayscale_and_non_square_pixels(host):
    host.aspect = 2.0
    result = preview.render_node_preview(host, node_name="Beauty", channels="depth.Z", max_size=100)
    assert set(result["rgb_mapping"].values()) == {"depth.Z"}
    assert (result["file_ref"]["width"], result["file_ref"]["height"]) == (100, 28)


def test_nested_source_uses_its_parent_and_restores_existing_context(host):
    group = Node(host, "Group", parent=host.root_node, name="Comp")
    host.root_node.children.remove(host.source)
    group.children.append(host.source)
    host.source._parent = group
    host.context = group
    result = preview.render_node_preview(host, node_name="Comp.Beauty")
    assert result["node_name"] == "Comp.Beauty"
    assert host.context is group
    assert host.graph == [host.root_node, host.source, group]


@pytest.mark.parametrize("failure", [RuntimeError("render failed"), KeyboardInterrupt()])
def test_render_failure_restores_state_and_removes_temp_file(host, monkeypatch, failure):
    paths = []

    def fail(write, *_args):
        paths.append(Path(write.values["file"]))
        raise failure

    monkeypatch.setattr(host, "execute", fail)
    with pytest.raises(type(failure)):
        preview.render_node_preview(host, node_name="Beauty", frame=99)
    assert not paths[0].parent.exists()
    assert host.graph == [host.root_node, host.source]
    assert host.current_frame == 12 and host.source.selected and host.root_node.proxy_mode
    assert not host.root_node.dirty and not preview._PREVIEWS
    host.Undo.enable.assert_called_once()


def test_display_transform_reuses_enumerated_color_contract(host, monkeypatch):
    transform = MagicMock()
    monkeypatch.setattr(preview, "set_display_transform", transform)
    result = preview.render_node_preview(host, node_name="Beauty", display="sRGB", view="ACES")
    assert result["display_transform"] == {"display": "sRGB", "view": "ACES"}
    assert transform.call_args.args[2:] == ("sRGB", "ACES")


@pytest.mark.parametrize(
    "params",
    [
        {"max_size": True},
        {"max_size": 2048},
        {"frame": 1.5},
        {"display": "sRGB"},
        {"channels": "missing"},
        {"node_name": "missing"},
    ],
)
def test_invalid_request_never_keeps_nodes_or_artifacts(host, params):
    with pytest.raises(ValueError):
        preview.render_node_preview(host, **{"node_name": "Beauty", **params})
    assert host.graph == [host.root_node, host.source]
    assert not preview._PREVIEWS


def test_preview_quota_and_cleanup_rejects_foreign_path(host, monkeypatch, tmp_path):
    monkeypatch.setattr(preview, "MAX_PREVIEWS", 1)
    result = preview.render_node_preview(host, node_name="Beauty")
    with pytest.raises(ValueError, match="limit reached"):
        preview.render_node_preview(host, node_name="Beauty")
    foreign = tmp_path / "foreign.png"
    foreign.write_bytes(b"preserve")
    with pytest.raises(ValueError, match="not owned"):
        preview.cleanup_preview(str(foreign))
    assert foreign.read_bytes() == b"preserve"
    preview.cleanup_preview(result["preview_id"])


def test_oversized_render_is_rejected_and_cleanup_still_runs(host, monkeypatch):
    def oversized(write, *_args):
        Path(write.values["file"]).write_bytes(_png(1025, 1))

    monkeypatch.setattr(host, "execute", oversized)
    with pytest.raises(RuntimeError, match="dimensions"):
        preview.render_node_preview(host, node_name="Beauty")
    assert host.graph == [host.root_node, host.source] and not preview._PREVIEWS


def test_cleanup_failure_keeps_dirty_flag_and_attempts_other_restoration(host, monkeypatch):
    monkeypatch.setattr(host, "delete", MagicMock(side_effect=RuntimeError("delete failed")))
    with pytest.raises(RuntimeError, match="state restoration failed"):
        preview.render_node_preview(host, node_name="Beauty", frame=40)
    assert host.current_frame == 12 and host.root_node.proxy_mode and host.source.selected
    assert host.root_node.dirty
    host.Undo.enable.assert_called_once()


def test_partial_group_creation_is_recovered_without_hiding_original_dirty_state(host, monkeypatch):
    original = host.nodes.Group
    host.root_node.dirty = True

    def fail(**kwargs):
        original(**kwargs)
        raise RuntimeError("onCreate failed")

    monkeypatch.setattr(host.nodes, "Group", fail)
    with pytest.raises(RuntimeError, match="onCreate failed"):
        preview.render_node_preview(host, node_name="Beauty")
    assert host.graph == [host.root_node, host.source]
    assert host.root_node.dirty and host.source.selected and host.root_node.proxy_mode
    host.Undo.enable.assert_called_once()


def test_preview_skill_contract():
    from dcc_mcp_core import validate_skill

    path = Path(__file__).parents[1] / "src" / "dcc_mcp_nuke" / "skills" / "nuke-preview"
    report = validate_skill(str(path))
    assert report.is_clean, report.issues
