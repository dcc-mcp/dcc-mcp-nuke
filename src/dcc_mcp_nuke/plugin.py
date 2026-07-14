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
