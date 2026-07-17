from pathlib import Path
from unittest.mock import MagicMock

import pytest

import dcc_mcp_nuke.compositing as compositing
from dcc_mcp_nuke.compositing import (
    _apply_layer_adjustments,
    _apply_output_format,
    _connect_merge,
    _nuke_path,
    _save_script,
    _set_read_frame_range,
    validate_manifest,
)
from dcc_mcp_nuke.plugin import is_gui_host


def manifest(tmp_path: Path):
    return {
        "script_path": str(tmp_path / "final.nk"),
        "output_path": str(tmp_path / "frames" / "final.%04d.png"),
        "first_frame": 1,
        "last_frame": 10,
        "layers": [
            {"name": "Beauty", "path": str(tmp_path / "beauty.%04d.exr")},
            {"name": "FX", "path": str(tmp_path / "fx.%04d.exr"), "operation": "plus"},
        ],
    }


def test_manifest_normalizes_layer_operations(tmp_path):
    result = validate_manifest(manifest(tmp_path))

    assert result["layers"][0]["operation"] == "over"
    assert result["layers"][1]["operation"] == "plus"
    assert result["layers"][0]["gain"] == 1.0
    assert result["layers"][0]["blur_size"] == 0.0
    assert result["width"] == 1920
    assert result["height"] == 1080


def test_manifest_normalizes_layer_adjustments(tmp_path):
    value = manifest(tmp_path)
    value["layers"][1].update(gain=2.5, blur_size=24)

    result = validate_manifest(value)

    assert result["layers"][1]["gain"] == 2.5
    assert result["layers"][1]["blur_size"] == 24.0


def test_manifest_normalizes_multilayer_exr_pipeline(tmp_path):
    value = manifest(tmp_path)
    value.update(
        required_layers=["rgba", "combinedemission", "CryptoMaterials"],
        output_colorspace="sRGB",
        output_datatype="16 bit",
    )
    value["layers"][0].update(
        channel="rgba",
        adjustments=[
            {"kind": "grade", "gain": 1.08, "gamma": 1.05},
            {
                "kind": "material_gain",
                "name": "Restrained_Orbital_Guides",
                "crypto_layer": "CryptoMaterials",
                "materials": ["/mat/Celestial_HUD_Emission"],
                "gain": 0.06,
            },
            {"kind": "blur", "size": 24},
        ],
    )

    result = validate_manifest(value)

    assert result["required_layers"] == ["rgba", "combinedemission", "CryptoMaterials"]
    assert result["output_colorspace"] == "sRGB"
    assert result["output_datatype"] == "16 bit"
    assert result["layers"][0]["channel"] == "rgba"
    assert [operation["kind"] for operation in result["layers"][0]["adjustments"]] == [
        "grade",
        "material_gain",
        "blur",
    ]
    assert result["layers"][0]["adjustments"][1]["materials"] == ["/mat/Celestial_HUD_Emission"]


def test_multilayer_adjustments_preserve_declared_order():
    def node(name, *knob_names):
        value = MagicMock()
        value.name.return_value = name
        knobs = {knob_name: MagicMock() for knob_name in knob_names}
        value.knobs.return_value = knobs
        value.__getitem__.side_effect = knobs.__getitem__
        return value, knobs

    grade, grade_knobs = node("Grade", "multiply", "gamma")
    matte, matte_knobs = node("Matte", "cryptoLayer", "matteList")
    attenuation, attenuation_knobs = node("Attenuation", "multiply")
    keymix, _ = node("Keymix", "channels", "maskChannel")
    blur, blur_knobs = node("Blur", "size")
    nuke = MagicMock()
    nuke.nodes.Grade.side_effect = [grade, attenuation]
    nuke.nodes.Cryptomatte.return_value = matte
    nuke.nodes.Keymix.return_value = keymix
    nuke.nodes.Blur.return_value = blur
    read, source = object(), object()
    layer = {
        "adjustments": [
            {"kind": "grade", "gain": 1.08, "gamma": 1.05},
            {
                "kind": "material_gain",
                "name": "Guide",
                "crypto_layer": "CryptoMaterials",
                "materials": ["/mat/Guide"],
                "gain": 0.06,
            },
            {"kind": "blur", "size": 24.0},
        ]
    }

    result, created = _apply_layer_adjustments(nuke, source, layer, 0, matte_source=read)

    grade.setInput.assert_called_once_with(0, source)
    grade_knobs["multiply"].setValue.assert_called_once_with(1.08)
    grade_knobs["gamma"].setValue.assert_called_once_with(1.05)
    matte.setInput.assert_called_once_with(0, read)
    matte_knobs["cryptoLayer"].setValue.assert_called_once_with("CryptoMaterials")
    matte_knobs["matteList"].setValue.assert_called_once_with("/mat/Guide")
    attenuation.setInput.assert_called_once_with(0, grade)
    attenuation_knobs["multiply"].setValue.assert_called_once_with(0.06)
    assert [call.args for call in keymix.setInput.call_args_list] == [(0, grade), (1, attenuation), (2, matte)]
    blur.setInput.assert_called_once_with(0, keymix)
    blur_knobs["size"].setValue.assert_called_once_with(24.0)
    assert result is blur
    assert created == ["Grade", "Matte", "Attenuation", "Keymix", "Blur"]


def test_multilayer_channel_requirements_and_write_options():
    class Read:
        @staticmethod
        def channels():
            return ["rgba.red", "combinedemission.red", "CryptoMaterials.red"]

    compositing._require_layers(Read(), ["rgba", "combinedemission", "CryptoMaterials"])

    with pytest.raises(RuntimeError, match="combinedvolume"):
        compositing._require_layers(Read(), ["combinedvolume"])

    shuffle = MagicMock()
    shuffle.knobs.return_value = {"in": MagicMock()}
    shuffle.__getitem__.side_effect = shuffle.knobs.return_value.__getitem__
    nuke = MagicMock()
    nuke.nodes.Shuffle.return_value = shuffle
    read = object()
    compositing._shuffle_layer(nuke, read, "combinedemission", 1)
    shuffle.setInput.assert_called_once_with(0, read)
    shuffle.knobs.return_value["in"].setValue.assert_called_once_with("combinedemission")

    write = MagicMock()
    write.knobs.return_value = {"colorspace": MagicMock(), "datatype": MagicMock()}
    write.__getitem__.side_effect = write.knobs.return_value.__getitem__
    compositing._set_write_options(write, {"output_colorspace": "sRGB", "output_datatype": "16 bit"})
    write.knobs.return_value["colorspace"].setValue.assert_called_once_with("sRGB")
    write.knobs.return_value["datatype"].setValue.assert_called_once_with("16 bit")


@pytest.mark.parametrize("field", ["gain", "blur_size"])
def test_manifest_rejects_negative_layer_adjustments(tmp_path, field):
    value = manifest(tmp_path)
    value["layers"][0][field] = -1

    with pytest.raises(ValueError, match=f"{field} must be non-negative"):
        validate_manifest(value)


def test_layer_adjustments_are_ordered_gain_then_blur():
    class Knob:
        def __init__(self):
            self.value = None

        def setValue(self, value):
            self.value = value

    class Node:
        def __init__(self, kind):
            self.kind = kind
            self.input = None
            self.node_name = kind
            self._knobs = {"multiply": Knob(), "size": Knob()}

        def setName(self, name):
            self.node_name = name

        def setInput(self, index, node):
            assert index == 0
            self.input = node

        def __getitem__(self, name):
            return self._knobs[name]

        def name(self):
            return self.node_name

    class Nodes:
        def __init__(self):
            self.created = []

        def Grade(self):
            node = Node("Grade")
            self.created.append(node)
            return node

        def Blur(self):
            node = Node("Blur")
            self.created.append(node)
            return node

    class Nuke:
        def __init__(self):
            self.nodes = Nodes()

    nuke = Nuke()
    source = Node("Read")
    result, created = _apply_layer_adjustments(nuke, source, {"gain": 3.0, "blur_size": 18.0}, 1)

    grade, blur = nuke.nodes.created
    assert grade.input is source
    assert grade["multiply"].value == 3.0
    assert blur.input is grade
    assert blur["size"].value == 18.0
    assert result is blur
    assert created == ["Grade_02", "Blur_02"]


def test_output_format_is_authoritative():
    class Knob:
        def __init__(self):
            self.value = None

        def setValue(self, value):
            self.value = value

    class Reformat:
        def __init__(self):
            self.node_name = "Reformat"
            self.input = None
            self._knobs = {name: Knob() for name in ("type", "format", "resize")}

        def setName(self, name):
            self.node_name = name

        def setInput(self, index, node):
            assert index == 0
            self.input = node

        def knobs(self):
            return self._knobs

        def __getitem__(self, name):
            return self._knobs[name]

        def name(self):
            return self.node_name

    class Nodes:
        def Reformat(self):
            return Reformat()

    class Nuke:
        nodes = Nodes()

    source = object()
    result, name = _apply_output_format(Nuke(), source, "dcc_mcp_output")

    assert result.input is source
    assert result["type"].value == "to format"
    assert result["format"].value == "dcc_mcp_output"
    assert result["resize"].value == "fit"
    assert name == "DCC_MCP_OUTPUT_FORMAT"


def test_manifest_rejects_relative_output(tmp_path):
    value = manifest(tmp_path)
    value["output_path"] = "relative.png"

    with pytest.raises(ValueError, match="output_path must be an absolute path"):
        validate_manifest(value)


def test_only_gui_nuke_processes_start_mcp():
    class Nuke:
        env = {"gui": True}

    class Worker:
        env = {"gui": False}

    assert is_gui_host(Nuke())
    assert not is_gui_host(Worker())


def test_nuke_paths_never_contain_windows_escape_sequences():
    assert _nuke_path(r"C:\artifacts\beauty.%04d.exr") == "C:/artifacts/beauty.%04d.exr"


def test_read_nodes_use_manifest_frame_range():
    class Knob:
        def __init__(self):
            self.value = None

        def setValue(self, value):
            self.value = value

    class Read:
        def __init__(self):
            self._knobs = {name: Knob() for name in ("first", "last", "origfirst", "origlast")}

        def knobs(self):
            return self._knobs

        def __getitem__(self, name):
            return self._knobs[name]

    read = Read()
    _set_read_frame_range(read, 1001, 1100)

    assert {name: knob.value for name, knob in read.knobs().items()} == {
        "first": 1001,
        "last": 1100,
        "origfirst": 1001,
        "origlast": 1100,
    }


def test_minus_connects_existing_comp_as_nuke_b_and_new_layer_as_nuke_a(tmp_path):
    value = manifest(tmp_path)
    value["layers"][1]["operation"] = "minus"

    assert validate_manifest(value)["layers"][1]["operation"] == "minus"

    class Merge:
        def __init__(self):
            self.inputs = {}

        def setInput(self, index, node):
            self.inputs[index] = node

    merge = Merge()
    _connect_merge(merge, background="sharp_emission", foreground="blurred_emission")

    assert merge.inputs == {0: "sharp_emission", 1: "blurred_emission"}


def test_script_save_is_non_interactive_and_idempotent():
    class Nuke:
        def __init__(self):
            self.saved = None

        def scriptSaveAs(self, path, overwrite):
            self.saved = (path, overwrite)

    nuke = Nuke()
    _save_script(nuke, r"C:\renders\final.nk")

    assert nuke.saved == ("C:/renders/final.nk", 1)


def test_build_layered_comp_positions_branches_and_output_deterministically(tmp_path):
    def node(name, *knob_names):
        value = MagicMock()
        state = {"name": name}
        knobs = {knob_name: MagicMock() for knob_name in knob_names}
        value.setName.side_effect = lambda new_name: state.update(name=new_name)
        value.name.side_effect = lambda: state["name"]
        value.knobs.return_value = knobs
        value.__getitem__.side_effect = knobs.__getitem__
        value.channels.return_value = ["emission.red", "CryptoMaterials.red"]
        return value

    beauty = node("Read1", "first", "last", "origfirst", "origlast")
    emission = node("Read2", "first", "last", "origfirst", "origlast")
    shuffle = node("Shuffle", "in")
    matte = node("Cryptomatte", "cryptoLayer", "matteList")
    attenuation = node("Grade", "multiply")
    keymix = node("Keymix", "channels", "maskChannel")
    blur = node("Blur", "size")
    merge = node("Merge", "operation")
    reformat = node("Reformat", "type", "format", "resize")
    write = node("Write", "file_type")
    root = node("Root", "first_frame", "last_frame", "fps", "format")
    nuke = MagicMock()
    nuke.allNodes.return_value = []
    nuke.root.return_value = root
    nuke.nodes.Read.side_effect = [beauty, emission]
    nuke.nodes.Shuffle.return_value = shuffle
    nuke.nodes.Cryptomatte.return_value = matte
    nuke.nodes.Grade.return_value = attenuation
    nuke.nodes.Keymix.return_value = keymix
    nuke.nodes.Blur.return_value = blur
    nuke.nodes.Merge2.return_value = merge
    nuke.nodes.Reformat.return_value = reformat
    nuke.nodes.Write.return_value = write
    value = manifest(tmp_path)
    value["layers"][1].update(
        channel="emission",
        adjustments=[
            {
                "kind": "material_gain",
                "crypto_layer": "CryptoMaterials",
                "materials": ["/mat/Sun"],
                "gain": 0.5,
            },
            {"kind": "blur", "size": 24},
        ],
    )

    compositing.build_layered_comp(nuke, value)

    assert beauty.setXYpos.call_args.args == (0, 0)
    assert emission.setXYpos.call_args.args == (320, 0)
    assert shuffle.setXYpos.call_args.args == (320, 100)
    assert attenuation.setXYpos.call_args.args == (320, 200)
    assert matte.setXYpos.call_args.args == (460, 200)
    assert keymix.setXYpos.call_args.args == (320, 300)
    assert blur.setXYpos.call_args.args == (320, 400)
    assert merge.setXYpos.call_args.args == (320, 500)
    assert reformat.setXYpos.call_args.args == (320, 600)
    assert write.setXYpos.call_args.args == (320, 700)


def test_node_positioning_falls_back_to_available_native_axis_setters():
    class Node:
        def setXpos(self, value):
            self.x = value

        def setYpos(self, value):
            self.y = value

    node = Node()

    compositing._set_node_position(node, 320, 100)
    compositing._set_node_position(object(), 0, 0)

    assert (node.x, node.y) == (320, 100)
