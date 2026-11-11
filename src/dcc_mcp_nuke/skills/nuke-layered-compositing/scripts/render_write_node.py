from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.diagnostics import verify_render_output


@skill_entry
def main(write_node: str, first_frame: int, last_frame: int, step: int = 1, **_kwargs):
    import nuke

    if last_frame < first_frame:
        return skill_error(
            "last_frame must be greater than or equal to first_frame",
            f"last_frame={last_frame} is less than first_frame={first_frame}",
        )
    if step < 1:
        return skill_error("step must be greater than or equal to 1", f"step={step} is less than 1")
    node = nuke.toNode(write_node)
    if node is None or node.Class() != "Write":
        return skill_error("Write node not found", write_node)
    output_path = node["file"].value()
    nuke.execute(node, int(first_frame), int(last_frame), int(step))
    # execute() returning is not proof that files exist. Report a separate,
    # explicit filesystem readback so a successful call cannot be mistaken for
    # verified output. This is not native Nuke render acceptance.
    try:
        verification = verify_render_output(
            output_path=output_path,
            first_frame=int(first_frame),
            last_frame=int(last_frame),
            step=int(step),
        )
    except (TypeError, ValueError) as exc:
        verification = {
            "verified": False,
            "mode": "unknown",
            "reason": "output path could not be checked: " + str(exc)[:256],
            "native_render_acceptance": "not_performed",
        }
    return skill_success(
        "Rendered Nuke Write node",
        verified=verification.get("verified") is True,
        write_node=write_node,
        first_frame=int(first_frame),
        last_frame=int(last_frame),
        step=int(step),
        output_path=output_path,
        output_verification=verification,
    )
