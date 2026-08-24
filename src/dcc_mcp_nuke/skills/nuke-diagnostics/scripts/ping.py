"""Bounded read-only Nuke readiness probe."""

from __future__ import annotations


def main() -> dict[str, object]:
    """Read the live host version after Core dispatches onto Nuke's main thread."""
    import nuke

    return {
        "ready": True,
        "dcc": "nuke",
        "host_version": str(nuke.env.get("NukeVersionString", "unknown")),
        "gui": bool(nuke.env.get("gui")),
    }
