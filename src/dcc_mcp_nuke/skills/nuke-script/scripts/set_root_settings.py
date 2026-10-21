from __future__ import annotations

from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.script_lifecycle import ScriptLifecycleError, set_root_settings


@skill_entry
def main(
    first_frame: int | None = None,
    last_frame: int | None = None,
    fps: float | None = None,
    format: str | None = None,
    **_kwargs,
):
    import nuke  # Lazy import: requires Nuke.

    try:
        result = set_root_settings(nuke, first_frame=first_frame, last_frame=last_frame, fps=fps, format=format)
    except ScriptLifecycleError as exc:
        return skill_error("Failed to set Nuke root settings", str(exc))
    except Exception:
        return skill_error("Failed to set Nuke root settings", "unexpected Nuke host failure")
    return skill_success("Set Nuke root settings", **result)
