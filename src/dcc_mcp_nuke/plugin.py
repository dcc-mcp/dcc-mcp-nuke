from dcc_mcp_nuke.server import start_server, stop_server


def is_gui_host(nuke_module=None) -> bool:
    """Exclude Frame Server and other headless Nuke worker processes."""
    if nuke_module is None:
        import nuke as nuke_module
    return bool(nuke_module.env.get("gui"))


def initialize() -> None:
    start_server()


def shutdown() -> None:
    stop_server()


def install_menu(nuke_module=None) -> None:
    """Install idempotent Nuke menu commands for adapter lifecycle control."""
    if nuke_module is None:
        import nuke as nuke_module
    root = nuke_module.menu("Nuke")
    menu = root.findItem("DCC-MCP") or root.addMenu("DCC-MCP")
    if menu.findItem("Start Server") is None:
        menu.addCommand("Start Server", initialize)
    if menu.findItem("Stop Server") is None:
        menu.addCommand("Stop Server", shutdown)
