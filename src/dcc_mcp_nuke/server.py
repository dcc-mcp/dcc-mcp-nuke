from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dcc_mcp_core import DccServerOptions, HostExecutionBridge
from dcc_mcp_core.server_base import DccServerBase

from dcc_mcp_nuke.__version__ import __version__
from dcc_mcp_nuke.dispatcher import NukeDispatcher

DEFAULT_PORT = 8765
SERVER_NAME = "dcc-mcp-nuke"
_server: Optional["NukeMcpServer"] = None


class NukeMcpServer(DccServerBase):
    def __init__(self, port: int = DEFAULT_PORT) -> None:
        options = DccServerOptions.from_env(
            "nuke",
            Path(__file__).resolve().parent / "skills",
            port=port,
            server_name=SERVER_NAME,
            server_version=__version__,
            execution_bridge=HostExecutionBridge(dispatcher=NukeDispatcher()),
        )
        super().__init__(options=options)

    def _version_string(self) -> str:
        try:
            import nuke

            return str(nuke.env.get("NukeVersionString", "Nuke"))
        except Exception:
            return "Nuke"


def start_server(port: Optional[int] = None) -> NukeMcpServer:
    global _server
    if _server is None or not _server.is_running:
        _server = NukeMcpServer(port or int(os.environ.get("DCC_MCP_NUKE_PORT", DEFAULT_PORT)))
        _server.register_builtin_actions()
        _server.start()
    return _server


def stop_server() -> None:
    global _server
    if _server is not None:
        _server.stop()
        _server = None
