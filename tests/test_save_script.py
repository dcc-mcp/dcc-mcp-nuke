import runpy
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

SCRIPT = Path(__file__).parents[1] / "src/dcc_mcp_nuke/skills/nuke-script/scripts/save_script.py"


def _nuke(monkeypatch, current):
    nuke = ModuleType("nuke")
    nuke.scriptName = Mock(return_value=str(current))
    nuke.scriptSave = Mock(return_value=True)

    def save_as(path, overwrite=0):
        target = Path(path)
        if target.exists() and not overwrite:
            raise FileExistsError(path)
        target.write_text("saved", encoding="utf-8")
        return True

    nuke.scriptSaveAs = Mock(side_effect=save_as)
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    return nuke


def test_save_current_existing_script_without_overwrite_prompt(tmp_path, monkeypatch):
    target = tmp_path / "current.nk"
    target.write_text("original", encoding="utf-8")
    nuke = _nuke(monkeypatch, target)
    result = runpy.run_path(str(SCRIPT))["main"].__wrapped__(str(target.parent / "." / target.name))
    assert result["success"] is True
    assert target.read_text(encoding="utf-8") == "saved"
    nuke.scriptSaveAs.assert_called_once_with(str(target.resolve()), overwrite=1)
    nuke.scriptSave.assert_not_called()


def test_save_as_new_path_for_unsaved_script(tmp_path, monkeypatch):
    target = tmp_path / "new.nk"
    nuke = _nuke(monkeypatch, "")
    nuke.scriptName.side_effect = RuntimeError("no filename available")
    result = runpy.run_path(str(SCRIPT))["main"].__wrapped__(str(target))
    assert result["success"] is True
    nuke.scriptSaveAs.assert_called_once_with(str(target.resolve()))
    nuke.scriptSave.assert_not_called()


def test_save_as_does_not_force_overwrite_other_script(tmp_path, monkeypatch):
    target = tmp_path / "other.nk"
    target.write_text("keep", encoding="utf-8")
    nuke = _nuke(monkeypatch, tmp_path / "current.nk")
    with pytest.raises(FileExistsError):
        runpy.run_path(str(SCRIPT))["main"].__wrapped__(str(target))
    assert target.read_text(encoding="utf-8") == "keep"
    nuke.scriptSave.assert_not_called()


def test_cancelled_save_is_not_success(tmp_path, monkeypatch):
    target = tmp_path / "current.nk"
    nuke = _nuke(monkeypatch, target)
    nuke.scriptSaveAs.side_effect = None
    nuke.scriptSaveAs.return_value = False
    result = runpy.run_path(str(SCRIPT))["main"].__wrapped__(str(target))
    assert result["success"] is False


def test_invalid_suffix_does_not_call_host(tmp_path, monkeypatch):
    nuke = _nuke(monkeypatch, "")
    result = runpy.run_path(str(SCRIPT))["main"].__wrapped__(str(tmp_path / "wrong.txt"))
    assert result["success"] is False
    nuke.scriptName.assert_not_called()
    nuke.scriptSave.assert_not_called()
    nuke.scriptSaveAs.assert_not_called()
