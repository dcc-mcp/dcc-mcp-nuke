from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.node_graph import auto_layout_nodes


@skill_entry
def main(node_names: list[str], **_kwargs):
    import nuke

    try:
        result = auto_layout_nodes(nuke, node_names)
    except (RuntimeError, TypeError, ValueError) as exc:
        return skill_error("Failed to auto-layout Nuke nodes", str(exc))
    except Exception:
        return skill_error("Failed to auto-layout Nuke nodes", "Required Nuke graph state could not be verified")
    return skill_success("Auto-laid out Nuke nodes", **result)
