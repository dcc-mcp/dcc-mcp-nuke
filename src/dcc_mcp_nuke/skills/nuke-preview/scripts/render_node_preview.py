from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.preview import render_node_preview


@skill_entry
def main(**kwargs):
    import nuke

    try:
        result = render_node_preview(nuke, **kwargs)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        return skill_error("Failed to render Nuke preview", str(exc))
    return skill_success("Rendered Nuke node preview", **result)
