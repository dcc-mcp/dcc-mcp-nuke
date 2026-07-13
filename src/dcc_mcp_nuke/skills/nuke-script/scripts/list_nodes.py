from dcc_mcp_core.skill import skill_entry, skill_success


@skill_entry
def main(**_kwargs):
    import nuke  # Lazy import: requires Nuke.

    nodes = [{"name": node.name(), "class": node.Class()} for node in nuke.allNodes(recurseGroups=True)]
    return skill_success("Listed Nuke nodes", node_count=len(nodes), nodes=nodes)
