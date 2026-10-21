from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.script_lifecycle import ScriptLifecycleError, get_root_settings


@skill_entry
def main(max_formats: int = 128, **_kwargs):
    import nuke  # Lazy import: requires Nuke.

    try:
        result = get_root_settings(nuke, max_formats=max_formats)
    except ScriptLifecycleError as exc:
        return skill_error("Failed to read Nuke root settings", str(exc))
    except Exception:
        return skill_error("Failed to read Nuke root settings", "unexpected Nuke host failure")
    return skill_success("Read Nuke root settings", **result)
