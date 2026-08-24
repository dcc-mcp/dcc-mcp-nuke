from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.node_graph import connect_input


@skill_entry
def main(node_name: str, input_index: int, source_node_name=None, **_kwargs):
    import nuke

    try:
        result = connect_input(nuke, node_name, input_index, source_node_name)
    except (RuntimeError, TypeError, ValueError) as exc:
        return skill_error("Failed to connect Nuke node input", str(exc))
    return skill_success("Updated Nuke node input", **result)
