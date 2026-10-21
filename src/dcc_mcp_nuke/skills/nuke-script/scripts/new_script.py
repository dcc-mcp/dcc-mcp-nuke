from __future__ import annotations

from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.script_lifecycle import ScriptLifecycleError, new_script


@skill_entry
def main(
    discard_unsaved_changes: bool = False,
    first_frame: int | None = None,
    last_frame: int | None = None,
    fps: float | None = None,
    format: str | None = None,
    **_kwargs,
):
    import nuke  # Lazy import: requires Nuke.

    try:
        result = new_script(
            nuke,
            discard_unsaved_changes=discard_unsaved_changes,
            first_frame=first_frame,
            last_frame=last_frame,
            fps=fps,
            format=format,
        )
    except ScriptLifecycleError as exc:
        return skill_error("Failed to start a new Nuke script", str(exc))
    except Exception:
        return skill_error("Failed to start a new Nuke script", "unexpected Nuke host failure")
    return skill_success("Started a new Nuke script", **result)
