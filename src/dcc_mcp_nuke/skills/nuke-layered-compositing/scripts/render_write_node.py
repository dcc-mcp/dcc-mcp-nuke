from dcc_mcp_core.skill import skill_entry, skill_error, skill_success


@skill_entry
def main(write_node: str, first_frame: int, last_frame: int, step: int = 1, **_kwargs):
    import nuke

    if last_frame < first_frame:
        return skill_error("last_frame must be greater than or equal to first_frame")
    if step < 1:
        return skill_error("step must be greater than or equal to 1")
    node = nuke.toNode(write_node)
    if node is None or node.Class() != "Write":
        return skill_error("Write node not found", write_node)
    nuke.execute(node, int(first_frame), int(last_frame), int(step))
    return skill_success(
        "Rendered Nuke Write node",
        write_node=write_node,
        first_frame=int(first_frame),
        last_frame=int(last_frame),
        step=int(step),
        output_path=node["file"].value(),
    )
