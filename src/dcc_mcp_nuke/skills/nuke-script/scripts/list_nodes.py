from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_nuke.node_graph import list_node_graph


@skill_entry
def main(max_nodes: int = 256, max_knobs_per_node: int = 64, **_kwargs):
    import nuke  # Lazy import: requires Nuke.

    result = list_node_graph(nuke, max_nodes=max_nodes, max_knobs_per_node=max_knobs_per_node)
    return skill_success("Listed Nuke nodes", **result)
