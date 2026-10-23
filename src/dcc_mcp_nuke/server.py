from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from dcc_mcp_core import DccServerOptions, HostExecutionBridge
from dcc_mcp_core.server_base import DccServerBase

from dcc_mcp_nuke.__version__ import __version__
from dcc_mcp_nuke.dispatcher import NukeDispatcher
from dcc_mcp_nuke.host_flavor import HostFlavorGate, HostFlavorReport, detect_host_flavor

if TYPE_CHECKING:
    from dcc_mcp_core.server import UiControlRuntimeOptions

DEFAULT_PORT = 0
SERVER_NAME = "dcc-mcp-nuke"
_server: Optional["NukeMcpServer"] = None


class NukeMcpServer(DccServerBase):
    def __init__(self, port: Optional[int] = None, *, ui_control: Optional[UiControlRuntimeOptions] = None) -> None:
        self._ui_control_runtime = ui_control
        self._host_dispatcher = NukeDispatcher()
        self._host_dispatcher.start()
        try:
            execution_bridge = HostExecutionBridge(
                dispatcher=self._host_dispatcher,
                host_dispatcher=self._host_dispatcher.host_dispatcher,
                default_thread_affinity="main",
                script_materialization_policy="auto",
            )
            self._nuke_execution_bridge = execution_bridge
            # Older supported Core versions do not accept this optional bootstrap argument.
            runtime_options = {} if ui_control is None else {"ui_control": ui_control}
            options = DccServerOptions.from_env(
                "nuke",
                Path(__file__).resolve().parent / "skills",
                port=port,
                server_name=SERVER_NAME,
                server_version=__version__,
                execution_bridge=execution_bridge,
                **runtime_options,
            )
            super().__init__(options=options)
            self._host_flavor_report = detect_host_flavor()
            self._host_flavor_gate = HostFlavorGate(self._host_flavor_report)
            self._publish_host_flavor_metadata()
            self.set_skill_load_transform(self._host_flavor_gate)
        except Exception:
            self._host_dispatcher.stop()
            raise

    @property
    def host_flavor_report(self) -> HostFlavorReport:
        """Host flavor classification captured when this server was built."""
        return self._host_flavor_report

    @property
    def host_flavor(self) -> str:
        """Running host flavor: ``nuke``, ``nukex``, or ``nukestudio``."""
        return self._host_flavor_report.flavor

    def _publish_host_flavor_metadata(self) -> None:
        """Advertise the host flavor through gateway instance metadata.

        Best effort only: a metadata backend that refuses the update must never
        stop the adapter from serving.
        """
        metadata = self._host_flavor_report.to_instance_metadata()
        config = getattr(self, "_config", None)
        existing = getattr(config, "instance_metadata", None)
        if isinstance(existing, dict):
            try:
                updated = dict(existing)
                updated.update(metadata)
                config.instance_metadata = updated
            except Exception:
                try:
                    existing.update(metadata)
                except Exception:
                    pass
        handle = getattr(self, "_handle", None)
        publish = getattr(handle, "update_gateway_metadata", None)
        if callable(publish):
            try:
                publish(metadata)
            except Exception:
                pass

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


def start_server(port: Optional[int] = None, *, ui_control: Optional[UiControlRuntimeOptions] = None) -> NukeMcpServer:
    """Start with optional operator-owned UI runtime options, never tool arguments."""
    global _server
    if _server is not None and _server.is_running and ui_control is not None:
        if ui_control != _server._ui_control_runtime:
            raise RuntimeError("Stop the Nuke MCP server before changing its UI control runtime")
    if _server is None or not _server.is_running:
        _server = NukeMcpServer(port, ui_control=ui_control) if ui_control is not None else NukeMcpServer(port)
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


def current_execution_bridge() -> Optional[HostExecutionBridge]:
    """Return the one live server bridge used by Nuke skill dispatch."""
    if _server is None:
        return None
    return _server._nuke_execution_bridge
