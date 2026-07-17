from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.gizmos import create_from_group


@skill_entry
def main(**request):
    import nuke

    try:
        result = create_from_group(nuke, **request)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return skill_error("Failed to create registered Gizmo", str(exc))
    return skill_success("Created registered Nuke Gizmo", **result)
