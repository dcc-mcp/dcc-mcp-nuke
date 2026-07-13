from dcc_mcp_nuke.server import start_server, stop_server


def initialize() -> None:
    start_server()


def shutdown() -> None:
    stop_server()
