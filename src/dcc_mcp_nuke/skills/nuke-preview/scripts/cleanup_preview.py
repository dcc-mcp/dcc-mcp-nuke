from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.preview import cleanup_preview


@skill_entry
def main(preview_id, **_kwargs):
    try:
        result = cleanup_preview(preview_id)
    except (OSError, ValueError) as exc:
        return skill_error("Failed to clean Nuke preview", str(exc))
    return skill_success("Removed Nuke node preview", **result)
