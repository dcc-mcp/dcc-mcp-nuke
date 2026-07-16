import runpy
from pathlib import Path
from unittest.mock import Mock

import dcc_mcp_nuke.plugin as plugin
from dcc_mcp_nuke.plugin import install_menu

MENU_ENTRY = Path(plugin.__file__).with_name("nuke_plugin") / "menu.py"


class Menu:
    def __init__(self):
        self.items = {}

    def findItem(self, name):
        return self.items.get(name)

    def addMenu(self, name):
        menu = Menu()
        self.items[name] = menu
        return menu

    def addCommand(self, name, callback):
        self.items[name] = callback


class Nuke:
    def __init__(self):
        self.root = Menu()

    def menu(self, name):
        assert name == "Nuke"
        return self.root


def test_install_menu_is_idempotent():
    nuke = Nuke()

    install_menu(nuke)
    install_menu(nuke)

    menu = nuke.root.items["DCC-MCP"]
    assert set(menu.items) == {"Start Server", "Stop Server"}


def test_menu_entry_only_runs_in_gui_hosts(monkeypatch):
    install = Mock()
    monkeypatch.setattr(plugin, "is_gui_host", lambda: False)
    monkeypatch.setattr(plugin, "install_menu", install)

    runpy.run_path(str(MENU_ENTRY))

    install.assert_not_called()


def test_menu_entry_does_not_block_startup_on_registration_failure(monkeypatch, caplog):
    monkeypatch.setattr(plugin, "is_gui_host", lambda: True)
    monkeypatch.setattr(plugin, "install_menu", Mock(side_effect=RuntimeError("menu unavailable")))

    runpy.run_path(str(MENU_ENTRY))

    assert "Failed to install DCC-MCP menu" in caplog.text
