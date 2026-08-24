"""Nuke startup entry point; loaded from NUKE_PATH."""

from pathlib import Path

from dcc_mcp_core import capture_bootstrap_errors

from dcc_mcp_nuke.__version__ import __version__

_CAPTURE = {
    "dcc_name": "nuke",
    "adapter_version": __version__,
    "min_core_version": "0.20.8",
    "log_dir": str(Path(__file__).resolve().parent.parent / ".dcc-mcp" / "logs"),
}

with capture_bootstrap_errors(phase="import", **_CAPTURE):
    from dcc_mcp_nuke.plugin import initialize, is_gui_host

if is_gui_host():
    with capture_bootstrap_errors(phase="startup", **_CAPTURE):
        initialize()
