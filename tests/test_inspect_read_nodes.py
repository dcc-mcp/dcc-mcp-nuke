import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock


def _load_module():
    path = (
        Path(__file__).parent.parent
        / "src"
        / "dcc_mcp_nuke"
        / "skills"
        / "nuke-script"
        / "scripts"
        / "inspect_read_nodes.py"
    )
    spec = importlib.util.spec_from_file_location("inspect_read_nodes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inspect_read_nodes_reports_multipart_parts_and_bounded_channels(monkeypatch):
    module = _load_module()
    read = MagicMock()
    read.name.return_value = "Beauty"
    read.Class.return_value = "Read"
    read.channels.return_value = ["rgba.red", "rgba.green", "rgba.blue"]
    knobs = {
        "file": MagicMock(),
        "first": MagicMock(),
        "last": MagicMock(),
        "part": MagicMock(),
    }
    knobs["file"].value.return_value = "C:/renders/beauty.%04d.exr"
    knobs["first"].value.return_value = 1
    knobs["last"].value.return_value = 1440
    knobs["part"].value.return_value = "Beauty.Combined"
    knobs["part"].values.return_value = ["Beauty.Combined", "Nebula.Combined"]
    read.knobs.return_value = knobs
    read.__getitem__.side_effect = knobs.__getitem__
    nuke = ModuleType("nuke")
    nuke.allNodes = MagicMock(return_value=[read])
    monkeypatch.setitem(sys.modules, "nuke", nuke)

    result = module.main.__wrapped__(channel_limit=2)

    assert result["context"]["read_nodes"] == [
        {
            "name": "Beauty",
            "file": "C:/renders/beauty.%04d.exr",
            "first_frame": 1,
            "last_frame": 1440,
            "part": "Beauty.Combined",
            "available_parts": ["Beauty.Combined", "Nebula.Combined"],
            "channel_count": 3,
            "channels": ["rgba.red", "rgba.green"],
            "channels_truncated": True,
        }
    ]
