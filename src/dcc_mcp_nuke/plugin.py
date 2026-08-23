"""Nuke plugin entry point: server lifecycle, menu, and clipboard helpers."""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

from dcc_mcp_nuke.server import start_server, stop_server

logger = logging.getLogger(__name__)

_DEFAULT_GATEWAY_PORT = 9765


def is_gui_host(nuke_module=None) -> bool:
    """Exclude Frame Server and other headless Nuke worker processes."""
    if nuke_module is None:
        import nuke as nuke_module
    return bool(nuke_module.env.get("gui"))


def initialize() -> None:
    start_server()


def shutdown() -> None:
    stop_server()


# ── server helpers ────────────────────────────────────────────────────────────


def _server_url() -> str:
    """Return the MCP URL of the running server, or an empty string."""
    import dcc_mcp_nuke.server as server_module

    srv = server_module._server
    if srv is None:
        return ""
    handle = getattr(srv, "_handle", None)
    if handle is None:
        return ""
    try:
        return handle.mcp_url() or ""
    except Exception:
        return ""


def _resolve_instance_id() -> Optional[str]:
    """Return the DCC MCP instance UUID from the running server, if available."""
    import dcc_mcp_nuke.server as server_module

    srv = server_module._server
    if srv is None:
        return None
    # Try the Python server object first (DccServerBase subclass)
    instance_id = getattr(srv, "instance_id", None)
    if instance_id:
        return str(instance_id)
    # Fallback: try the Rust core server attribute
    core = getattr(srv, "_server", None)
    if core is not None:
        instance_id = getattr(core, "instance_id", None)
        if instance_id:
            return str(instance_id)
    return None


def _gateway_url() -> str:
    """Return the gateway base URL from env, or empty string if disabled."""
    raw = os.environ.get("DCC_MCP_GATEWAY_PORT", str(_DEFAULT_GATEWAY_PORT))
    try:
        port = int(raw)
    except ValueError:
        port = 0
    if port <= 0:
        return ""
    return f"http://127.0.0.1:{port}"


# ── clipboard ─────────────────────────────────────────────────────────────────


def _set_clipboard_text(text: str) -> None:
    """Set the system clipboard text, trying PySide2 then PySide6."""
    for binding in ("PySide2", "PySide6"):
        try:
            mod = __import__(binding)
            app = mod.QtWidgets.QApplication.instance()
            if app is not None:
                app.clipboard().setText(text)
                return
        except Exception:
            continue
    raise RuntimeError("Unable to access system clipboard (no PySide binding available)")


# ── menu actions ──────────────────────────────────────────────────────────────


def _copy_instance_id() -> None:
    """Copy the DCC MCP instance UUID to the system clipboard."""
    import nuke

    instance_id = _resolve_instance_id()
    if not instance_id:
        nuke.message("DCC MCP: Instance ID not available. Is the server running?")
        return
    try:
        _set_clipboard_text(instance_id)
    except RuntimeError as exc:
        nuke.message(str(exc))
        return
    logger.info("DCC MCP: Instance ID copied to clipboard: %s", instance_id)
    nuke.message(f"Instance ID copied to clipboard:\n{instance_id}")


def _show_server_info() -> None:
    """Show server status information in a dialog."""
    import nuke

    instance_id = _resolve_instance_id()
    instance_url = _server_url()

    try:
        nuke_version = str(nuke.env.get("NukeVersionString", "Nuke"))
    except Exception:
        nuke_version = "unknown"

    gateway_port_str = os.environ.get("DCC_MCP_GATEWAY_PORT", str(_DEFAULT_GATEWAY_PORT))
    try:
        gp = int(gateway_port_str)
    except ValueError:
        gp = _DEFAULT_GATEWAY_PORT
    gateway_display = "disabled" if gp <= 0 else str(gp)

    core_version = "unknown"
    try:
        from dcc_mcp_core.server_base import _package_version

        core_version = _package_version() or "unknown"
    except Exception:
        pass

    from dcc_mcp_nuke.__version__ import __version__

    msg = (
        f"Instance UUID: {instance_id or 'N/A'}\n"
        f"DCC: Nuke {nuke_version}\n"
        f"PID: {os.getpid()}\n"
        f"MCP URL: {instance_url or 'N/A'}\n"
        f"Gateway Port: {gateway_display}\n"
        f"Core Version: {core_version}\n"
        f"Adapter Version: {__version__}\n"
        f"Python: {sys.version.split()[0]}"
    )
    nuke.message(msg)


def _show_about() -> None:
    """Show about dialog with version information."""
    import nuke

    try:
        nuke_version = str(nuke.env.get("NukeVersionString", "Nuke"))
    except Exception:
        nuke_version = "unknown"

    from dcc_mcp_nuke.__version__ import __version__

    msg = (
        f"dcc-mcp-nuke v{__version__}\n"
        f"Nuke {nuke_version}\n"
        f"Python {sys.version.split()[0]}\n\n"
        "DCC MCP — AI-driven DCC automation.\n"
        "https://github.com/dcc-mcp/dcc-mcp-nuke"
    )
    nuke.message(msg)


def _open_openapi_docs() -> None:
    """Open the DCC service OpenAPI docs (Swagger UI) in the default browser."""
    import nuke

    base = _server_url().replace("/mcp", "")
    if not base:
        nuke.message("MCP server is not running.")
        return
    import webbrowser

    webbrowser.open(base + "/docs")


def _open_admin_panel() -> None:
    """Open the gateway admin panel in the default browser."""
    import nuke

    gw = _gateway_url()
    if not gw:
        nuke.message("Gateway is disabled (DCC_MCP_GATEWAY_PORT=0). Cannot open admin panel.")
        return
    import webbrowser

    webbrowser.open(gw + "/admin")


# ── menu registration ─────────────────────────────────────────────────────────


def install_menu(nuke_module=None) -> None:
    """Install idempotent Nuke menu commands for adapter lifecycle control.

    Unified menu structure (PIP-2794):
      - OpenAPI Docs
      - Admin Panel
      - ---
      - Copy Instance ID
      - Server Info
      - ---
      - Start Server
      - Stop Server
      - ---
      - About DCC MCP
    """
    if nuke_module is None:
        import nuke as nuke_module
    root = nuke_module.menu("Nuke")
    menu = root.findItem("DCC-MCP") or root.addMenu("DCC-MCP")

    # Track registered items to keep idempotency
    existing = set()
    for name in (
        "OpenAPI Docs",
        "Admin Panel",
        "Copy Instance ID",
        "Server Info",
        "Start Server",
        "Stop Server",
        "About DCC MCP",
    ):
        if menu.findItem(name) is not None:
            existing.add(name)

    if "OpenAPI Docs" not in existing:
        menu.addCommand("OpenAPI Docs", _open_openapi_docs)
    if "Admin Panel" not in existing:
        menu.addCommand("Admin Panel", _open_admin_panel)

    menu.addSeparator()

    if "Copy Instance ID" not in existing:
        menu.addCommand("Copy Instance ID", _copy_instance_id)
    if "Server Info" not in existing:
        menu.addCommand("Server Info", _show_server_info)

    menu.addSeparator()

    if "Start Server" not in existing:
        menu.addCommand("Start Server", initialize)
    if "Stop Server" not in existing:
        menu.addCommand("Stop Server", shutdown)

    menu.addSeparator()

    if "About DCC MCP" not in existing:
        menu.addCommand("About DCC MCP", _show_about)
