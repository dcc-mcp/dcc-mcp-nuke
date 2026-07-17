from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.gizmos import validate


@skill_entry
def main(**request):
    import nuke

    try:
        result = validate(nuke, **request)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return skill_error("Failed to validate registered Gizmo", str(exc))
    return skill_success("Validated registered Nuke Gizmo", **result)
