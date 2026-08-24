from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.node_graph import set_knob


@skill_entry
def main(node_name: str, knob_name: str, value, **_kwargs):
    import nuke

    try:
        result = set_knob(nuke, node_name, knob_name, value)
    except (RuntimeError, TypeError, ValueError) as exc:
        return skill_error("Failed to set Nuke knob", str(exc))
    return skill_success("Set Nuke knob", **result)
