from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.node_graph import create_node


@skill_entry
def main(node_class: str, name=None, x=None, y=None, **_kwargs):
    import nuke

    try:
        result = create_node(nuke, node_class, name=name, x=x, y=y)
    except (RuntimeError, TypeError, ValueError) as exc:
        return skill_error("Failed to create Nuke node", str(exc))
    return skill_success("Created Nuke node", **result)
