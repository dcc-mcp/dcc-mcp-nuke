from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.node_graph import delete_node


@skill_entry
def main(node_name: str, **_kwargs):
    import nuke

    try:
        result = delete_node(nuke, node_name)
    except (RuntimeError, TypeError, ValueError) as exc:
        return skill_error("Failed to delete Nuke node", str(exc))
    return skill_success("Deleted Nuke node", **result)
