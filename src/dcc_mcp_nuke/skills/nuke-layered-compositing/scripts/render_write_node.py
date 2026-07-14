from dcc_mcp_core.skill import skill_entry, skill_error, skill_success


@skill_entry
def main(write_node: str, first_frame: int, last_frame: int, **_kwargs):
    import nuke

    if last_frame < first_frame:
        return skill_error("last_frame must be greater than or equal to first_frame")
    node = nuke.toNode(write_node)
    if node is None or node.Class() != "Write":
        return skill_error("Write node not found", write_node)
    nuke.execute(node, int(first_frame), int(last_frame))
    return skill_success(
        "Rendered Nuke Write node",
        write_node=write_node,
        first_frame=int(first_frame),
        last_frame=int(last_frame),
        output_path=node["file"].value(),
    )
