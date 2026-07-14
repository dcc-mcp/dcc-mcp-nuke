"""Nuke startup entry point; loaded from NUKE_PATH."""

from dcc_mcp_nuke.plugin import initialize, is_gui_host

if is_gui_host():
    initialize()
