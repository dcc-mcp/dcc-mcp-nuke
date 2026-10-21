from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.color_management import ColorManagementError, set_display_transform


@skill_entry
def main(node_name: str, display: str, view: str, **_kwargs):
    import nuke  # Lazy import: requires Nuke.

    try:
        result = set_display_transform(nuke, node_name, display, view)
    except ColorManagementError as exc:
        return skill_error("Failed to set Nuke OCIODisplay transform", str(exc))
    except Exception:
        return skill_error("Failed to set Nuke OCIODisplay transform", "unexpected Nuke host failure")
    return skill_success("Set Nuke OCIODisplay transform", **result)
