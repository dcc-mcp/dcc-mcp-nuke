from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_nuke.input_health import check_input_health


@skill_entry
def main(
    include_write_ranges=False,
    required_node_classes=None,
    max_references=256,
    max_issues=256,
    max_frames=10000,
    max_files=10000,
    small_frame_ratio=0.1,
    **_kwargs,
):
    import nuke

    result = check_input_health(
        nuke,
        include_write_ranges=include_write_ranges,
        required_node_classes=required_node_classes,
        max_references=max_references,
        max_issues=max_issues,
        max_frames=max_frames,
        max_files=max_files,
        small_frame_ratio=small_frame_ratio,
    )
    return skill_success("Checked Nuke input health", **result)
