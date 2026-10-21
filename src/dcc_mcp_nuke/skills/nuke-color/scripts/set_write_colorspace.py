from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.color_management import ColorManagementError, set_write_colorspace


@skill_entry
def main(node_name: str, colorspace: str, **_kwargs):
    import nuke  # Lazy import: requires Nuke.

    try:
        result = set_write_colorspace(nuke, node_name, colorspace)
    except ColorManagementError as exc:
        return skill_error("Failed to set Nuke Write colorspace", str(exc))
    except Exception:
        return skill_error("Failed to set Nuke Write colorspace", "unexpected Nuke host failure")
    return skill_success("Set Nuke Write colorspace", **result)
