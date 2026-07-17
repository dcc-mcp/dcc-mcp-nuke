from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.gizmos import instantiate


@skill_entry
def main(**request):
    import nuke

    try:
        result = instantiate(nuke, **request)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return skill_error("Failed to instantiate registered Gizmo", str(exc))
    return skill_success("Instantiated registered Nuke Gizmo", **result)
