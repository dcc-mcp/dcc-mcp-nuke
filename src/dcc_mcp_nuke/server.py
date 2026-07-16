from __future__ import annotations

from pathlib import Path
from typing import Optional

from dcc_mcp_core import DccServerOptions, HostExecutionBridge
from dcc_mcp_core.server_base import DccServerBase

from dcc_mcp_nuke.__version__ import __version__
from dcc_mcp_nuke.dispatcher import NukeDispatcher

DEFAULT_PORT = 0
SERVER_NAME = "dcc-mcp-nuke"
_server: Optional["NukeMcpServer"] = None


class NukeMcpServer(DccServerBase):
    def __init__(self, port: Optional[int] = None) -> None:
        self._host_dispatcher = NukeDispatcher()
        self._host_dispatcher.start()
        options = DccServerOptions.from_env(
            "nuke",
            Path(__file__).resolve().parent / "skills",
            port=port,
            server_name=SERVER_NAME,
            server_version=__version__,
            execution_bridge=HostExecutionBridge(
                dispatcher=self._host_dispatcher,
                host_dispatcher=self._host_dispatcher.host_dispatcher,
                default_thread_affinity="main",
            ),
        )
        try:
            super().__init__(options=options)
        except Exception:
            self._host_dispatcher.stop()
            raise

    def stop(self) -> None:
        """Stop HTTP serving before detaching the Nuke UI queue pump."""
        try:
            super().stop()
        finally:
            self._host_dispatcher.stop()

    def _version_string(self) -> str:
        try:
            import nuke

            return str(nuke.env.get("NukeVersionString", "Nuke"))
        except Exception:
            return "Nuke"


def start_server(port: Optional[int] = None) -> NukeMcpServer:
    global _server
    if _server is None or not _server.is_running:
        _server = NukeMcpServer(port)
        try:
            _server.register_builtin_actions()
            _server.start()
        except Exception:
            _server.stop()
            _server = None
            raise
    return _server


def stop_server() -> None:
    global _server
    if _server is not None:
        _server.stop()
        _server = None
