import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest


def _load_render_module():
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
    return module


def test_render_write_node_forwards_step(monkeypatch):
    module = _load_render_module()

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


@pytest.mark.parametrize(
    ("first_frame", "last_frame", "step", "error"),
    [(2, 1, 1, "last_frame=1 is less than first_frame=2"), (1, 1, 0, "step=0 is less than 1")],
)
def test_render_write_node_returns_error_for_invalid_range_or_step(monkeypatch, first_frame, last_frame, step, error):
    module = _load_render_module()
    monkeypatch.setitem(sys.modules, "nuke", ModuleType("nuke"))

    result = module.main.__wrapped__("Final", first_frame, last_frame, step=step)

    assert result["success"] is False
    assert result["error"] == error
