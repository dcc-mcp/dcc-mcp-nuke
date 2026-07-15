from dcc_mcp_nuke.plugin import install_menu


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
