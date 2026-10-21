from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.color_management import ColorManagementError, set_ocio_config


@skill_entry
def main(config: str, **_kwargs):
    import nuke  # Lazy import: requires Nuke.

    try:
        result = set_ocio_config(nuke, config)
    except ColorManagementError as exc:
        return skill_error("Failed to set Nuke OCIO config", str(exc))
    except Exception:
        return skill_error("Failed to set Nuke OCIO config", "unexpected Nuke host failure")
    return skill_success("Set Nuke OCIO config", **result)
