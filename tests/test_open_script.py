import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock


def _load_open_module():
    path = (
        Path(__file__).parent.parent / "src" / "dcc_mcp_nuke" / "skills" / "nuke-script" / "scripts" / "open_script.py"
    )
    spec = importlib.util.spec_from_file_location("open_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_nuke(script_path: Path, *, node_count: int = 3):
    first_frame = MagicMock()
    first_frame.value.return_value = 1
    last_frame = MagicMock()
    last_frame.value.return_value = 1440
    root = MagicMock()
    root.__getitem__.side_effect = {
        "first_frame": first_frame,
        "last_frame": last_frame,
    }.__getitem__

    nuke = ModuleType("nuke")
    nuke.scriptOpen = MagicMock()
    nuke.scriptName = MagicMock(return_value=str(script_path))
    nuke.root = MagicMock(return_value=root)
    nuke.allNodes = MagicMock(return_value=[object()] * node_count)
    return nuke


def test_open_script_loads_absolute_nk_and_reports_postconditions(tmp_path, monkeypatch):
    module = _load_open_module()
    target = tmp_path / "final.nk"
    target.write_text("Root {}", encoding="utf-8")
    nuke = _fake_nuke(target)
    monkeypatch.setitem(sys.modules, "nuke", nuke)

    result = module.main.__wrapped__(str(target))

    nuke.scriptOpen.assert_called_once_with(str(target.resolve()))
    assert result["success"] is True
    assert result["context"] == {
        "path": str(target.resolve()),
        "first_frame": 1,
        "last_frame": 1440,
        "node_count": 3,
    }


def test_open_script_rejects_relative_path_before_host_mutation(monkeypatch):
    module = _load_open_module()
    nuke = _fake_nuke(Path("relative.nk"))
    monkeypatch.setitem(sys.modules, "nuke", nuke)

    result = module.main.__wrapped__("relative.nk")

    assert result["success"] is False
    assert result["error"] == "path must be absolute"
    nuke.scriptOpen.assert_not_called()


def test_open_script_rejects_missing_or_non_nk_path(tmp_path, monkeypatch):
    module = _load_open_module()
    nuke = _fake_nuke(tmp_path / "missing.nk")
    monkeypatch.setitem(sys.modules, "nuke", nuke)

    missing = module.main.__wrapped__(str(tmp_path / "missing.nk"))
    not_nk_path = tmp_path / "notes.txt"
    not_nk_path.write_text("not a Nuke script", encoding="utf-8")
    not_nk = module.main.__wrapped__(str(not_nk_path))

    assert missing["error"] == "script does not exist"
    assert not_nk["error"] == "script path must end in .nk"
    nuke.scriptOpen.assert_not_called()


def test_open_script_rejects_failed_path_postcondition(tmp_path, monkeypatch):
    module = _load_open_module()
    target = tmp_path / "requested.nk"
    target.write_text("Root {}", encoding="utf-8")
    nuke = _fake_nuke(tmp_path / "different.nk")
    monkeypatch.setitem(sys.modules, "nuke", nuke)

    result = module.main.__wrapped__(str(target))

    assert result["success"] is False
    assert result["error"] == "opened path does not match requested script"
