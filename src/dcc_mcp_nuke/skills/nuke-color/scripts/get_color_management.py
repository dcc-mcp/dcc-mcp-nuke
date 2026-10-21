from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.color_management import ColorManagementError, describe_color_management


@skill_entry
def main(max_colorspaces: int = 256, max_ocio_configs: int = 128, **_kwargs):
    import nuke  # Lazy import: requires Nuke.

    try:
        result = describe_color_management(
            nuke,
            max_colorspaces=max_colorspaces,
            max_ocio_configs=max_ocio_configs,
        )
    except ColorManagementError as exc:
        return skill_error("Failed to inspect Nuke color management", str(exc))
    except Exception:
        return skill_error("Failed to inspect Nuke color management", "unexpected Nuke host failure")
    return skill_success("Inspected Nuke color management", **result)
