import base64
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# A genuinely decodable 2x2 RGB PNG (IHDR + IDAT + IEND, valid CRCs).
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR42mO4Iycnd0eOAUIBACAqBGGFHUPdAAAAAElFTkSuQmCC"
)


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


def test_render_write_node_proves_real_output_files_after_execute(monkeypatch, tmp_path):
    module = _load_render_module()
    directory = tmp_path / "renders"
    directory.mkdir()
    for frame in (1, 2, 3):
        (directory / f"final.{frame:04d}.png").write_bytes(_PNG)

    write = MagicMock()
    write.Class.return_value = "Write"
    write.__getitem__.return_value.value.return_value = str(directory / "final.%04d.png")
    nuke = ModuleType("nuke")
    nuke.toNode = MagicMock(return_value=write)
    nuke.execute = MagicMock()
    monkeypatch.setitem(sys.modules, "nuke", nuke)

    result = module.main.__wrapped__("Final", 1, 3, step=1)

    nuke.execute.assert_called_once_with(write, 1, 3, 1)
    assert result["success"] is True
    assert result["postcondition"]["verified"] is True
    verification = result["context"]["output_verification"]
    assert verification["verified"] is True
    assert verification["mode"] == "sequence"
    assert verification["checked_frame_count"] == 3
    assert verification["native_render_acceptance"] == "not_performed"


def test_successful_execute_without_output_files_is_not_verified(monkeypatch, tmp_path):
    """A returning nuke.execute must not be reported as verified output."""
    module = _load_render_module()

    write = MagicMock()
    write.Class.return_value = "Write"
    write.__getitem__.return_value.value.return_value = str(tmp_path / "missing.%04d.png")
    nuke = ModuleType("nuke")
    nuke.toNode = MagicMock(return_value=write)
    nuke.execute = MagicMock(return_value=None)
    monkeypatch.setitem(sys.modules, "nuke", nuke)

    result = module.main.__wrapped__("Final", 1, 3, step=1)

    nuke.execute.assert_called_once_with(write, 1, 3, 1)
    assert result["success"] is True
    assert result["postcondition"]["verified"] is False
    verification = result["context"]["output_verification"]
    assert verification["verified"] is False
    assert verification["missing_frames"] == [1, 2, 3]
    assert "NOT native Nuke render acceptance" in verification["scope"]


def test_unverifiable_output_path_is_explicitly_unknown(monkeypatch):
    module = _load_render_module()

    write = MagicMock()
    write.Class.return_value = "Write"
    write.__getitem__.return_value.value.return_value = None
    nuke = ModuleType("nuke")
    nuke.toNode = MagicMock(return_value=write)
    nuke.execute = MagicMock()
    monkeypatch.setitem(sys.modules, "nuke", nuke)

    result = module.main.__wrapped__("Final", 1, 1)

    assert result["success"] is True
    assert result["postcondition"]["verified"] is False
    verification = result["context"]["output_verification"]
    assert verification["mode"] == "unknown"
    assert verification["native_render_acceptance"] == "not_performed"
