import json
from collections.abc import Mapping

from dcc_mcp_core.skill import skill_entry, skill_error, skill_success


@skill_entry
def main(node_name, **_kwargs):
    import nuke  # Lazy import: requires Nuke.

    node = nuke.toNode(node_name)
    if node is None:
        return skill_error("Failed to inspect Gizmo", "node not found")

    issues = []
    manifest = {}
    knobs = node.knobs()
    manifest_knob = knobs.get("dcc_mcp_asset_manifest")
    if manifest_knob is None:
        issues.append("DCC MCP asset manifest is missing")
    else:
        try:
            manifest = json.loads(manifest_knob.value())
        except (TypeError, ValueError):
            issues.append("DCC MCP asset manifest is invalid")

    exposed = []
    for item in manifest.get("exposed_knobs", []):
        name = item.get("name") if isinstance(item, Mapping) else item
        if not isinstance(name, str):
            issues.append("exposed knob manifest entry is invalid")
            continue
        knob = knobs.get(name)
        if knob is None:
            issues.append("exposed knob is missing: {}".format(name))
            continue
        get_link = getattr(knob, "getLink", None)
        exposed.append(
            {
                "name": name,
                "label": knob.label(),
                "link": get_link() if get_link else None,
            }
        )

    if node.hasError():
        issues.append("Gizmo reports a Nuke evaluation error")
    children = node.nodes() if hasattr(node, "nodes") else []
    if not hasattr(node, "nodes"):
        issues.append("node is not a Group or Gizmo")

    return skill_success(
        "Inspected Nuke Gizmo",
        node_name=node.name(),
        node_class=node.Class(),
        asset_name=manifest.get("gizmo_id") or manifest.get("name"),
        version=manifest.get("version"),
        valid=not issues,
        issues=issues,
        child_nodes=[{"name": child.name(), "class": child.Class()} for child in children],
        exposed_knobs=exposed,
    )
