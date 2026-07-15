import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock


def test_render_write_node_forwards_step(monkeypatch):
    path = (
        Path(__file__).parent.parent
        / "src"
        / "dcc_mcp_nuke"
        / "skills"
        / "nuke-layered-compositing"
        / "scripts"
        / "render_write_node.py"
    )
    spec = importlib.util.spec_from_file_location("render_write_node", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    write = MagicMock()
    write.Class.return_value = "Write"
    write.__getitem__.return_value.value.return_value = "/renders/final.%04d.png"
    nuke = ModuleType("nuke")
    nuke.toNode = MagicMock(return_value=write)
    nuke.execute = MagicMock()
    monkeypatch.setitem(sys.modules, "nuke", nuke)

    result = module.main.__wrapped__("Final", 1, 480, step=4)

    nuke.execute.assert_called_once_with(write, 1, 480, 4)
    assert result["context"]["step"] == 4
