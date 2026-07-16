from dcc_mcp_core.skill import skill_entry, skill_error, skill_success


def _value(node, name):
    knob = node.knobs().get(name)
    return None if knob is None else knob.value()


@skill_entry
def main(node_name=None, channel_limit=256, **_kwargs):
    import nuke  # Lazy import: requires Nuke.

    if not 1 <= channel_limit <= 2048:
        return skill_error("channel_limit must be between 1 and 2048")

    read_nodes = []
    for node in nuke.allNodes(recurseGroups=True):
        if node.Class() != "Read" or (node_name and node.name() != node_name):
            continue
        channels = list(node.channels())
        part = node.knobs().get("part")
        read_nodes.append(
            {
                "name": node.name(),
                "file": _value(node, "file"),
                "first_frame": _value(node, "first"),
                "last_frame": _value(node, "last"),
                "part": None if part is None else part.value(),
                "available_parts": [] if part is None or not hasattr(part, "values") else list(part.values()),
                "channel_count": len(channels),
                "channels": channels[:channel_limit],
                "channels_truncated": len(channels) > channel_limit,
            }
        )
    return skill_success("Inspected Nuke Read nodes", read_nodes=read_nodes)
