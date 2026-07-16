"""Nuke menu entry point; loaded from NUKE_PATH after init.py."""

import logging

try:
    from dcc_mcp_nuke.plugin import install_menu, is_gui_host

    if is_gui_host():
        install_menu()
except Exception:
    logging.getLogger(__name__).exception("Failed to install DCC-MCP menu")
