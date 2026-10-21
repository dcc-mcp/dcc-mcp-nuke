from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.script_lifecycle import ScriptLifecycleError, paste_nodes


@skill_entry
def main(path: str, max_reported_nodes: int = 256, **_kwargs):
    import nuke  # Lazy import: requires Nuke.

    try:
        result = paste_nodes(nuke, path, max_reported_nodes=max_reported_nodes)
    except ScriptLifecycleError as exc:
        return skill_error("Failed to paste Nuke nodes", str(exc))
    except Exception:
        return skill_error("Failed to paste Nuke nodes", "unexpected Nuke host failure")
    return skill_success("Pasted Nuke nodes", **result)
