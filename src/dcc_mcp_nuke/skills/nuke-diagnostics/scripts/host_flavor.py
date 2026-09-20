"""Read-only Nuke host flavor and capability report."""

from __future__ import annotations

from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_nuke.host_flavor import STUDIO_CAPABILITIES, detect_host_flavor


@skill_entry
def main(**_kwargs) -> dict:
    """Report which Nuke entry point is running and what it can do."""
    report = detect_host_flavor()
    return skill_success(
        f"Nuke host flavor is {report.flavor}",
        dcc="nuke",
        flavor=report.flavor,
        gui=report.gui,
        host_version=report.host_version,
        executable=report.executable,
        hiero_importable=report.hiero_importable,
        signals=list(report.signals),
        capabilities=list(report.capabilities),
        studio_capabilities=sorted(
            set(report.capabilities) & set(STUDIO_CAPABILITIES),
        ),
    )
