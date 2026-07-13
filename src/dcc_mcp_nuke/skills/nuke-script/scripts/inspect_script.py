from dcc_mcp_core.skill import skill_entry, skill_success


@skill_entry
def main(**_kwargs):
    import nuke  # Lazy import: requires Nuke.

    root = nuke.root()
    selected = nuke.selectedNodes()
    return skill_success(
        "Inspected Nuke script",
        path=nuke.scriptName(),
        first_frame=root["first_frame"].value(),
        last_frame=root["last_frame"].value(),
        selected_nodes=[node.name() for node in selected],
    )
