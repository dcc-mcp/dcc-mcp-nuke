import json
import re
from pathlib import Path

from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.gizmos import _publish_file, _temporary_path

_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _invalid(error):
    return skill_error("Invalid Gizmo package request", error)


def _normalize_gizmo(path):
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.strip() == "Group {":
            indent = line[: len(line) - len(line.lstrip())]
            ending = "\n" if line.endswith("\n") else ""
            lines[index] = f"{indent}Gizmo {{{ending}"
            with path.open("w", encoding="utf-8", newline="") as stream:
                stream.write("".join(lines))
            return
    raise ValueError("serialized Group header was not found")


@skill_entry
def main(
    node_names,
    gizmo_name,
    output_path,
    exposed_knobs=None,
    version="1.0.0",
    overwrite=False,
    **_kwargs,
):
    candidate = Path(output_path).expanduser()
    if not candidate.is_absolute():
        return _invalid("output_path must be absolute")
    target = candidate.resolve()
    if target.suffix.lower() != ".gizmo":
        return _invalid("output_path must end in .gizmo")
    if target.exists() and not overwrite:
        return _invalid("output_path already exists; set overwrite=true to replace it")
    if not _IDENTIFIER.fullmatch(gizmo_name or ""):
        return _invalid("gizmo_name must be a Nuke identifier")
    if not isinstance(version, str) or not version.strip():
        return _invalid("version must be a non-empty string")
    if not node_names or len(set(node_names)) != len(node_names):
        return _invalid("node_names must be a non-empty unique list")

    exposed = list(exposed_knobs or [])
    exposed_names = [item.get("name") for item in exposed if isinstance(item, dict)]
    if len(exposed_names) != len(exposed) or len(set(exposed_names)) != len(exposed_names):
        return _invalid("exposed_knobs must have unique names")
    if any(not _IDENTIFIER.fullmatch(name or "") for name in exposed_names):
        return _invalid("exposed knob names must be Nuke identifiers")

    import nuke  # Lazy import: requires Nuke.

    nodes = [nuke.toNode(name) for name in node_names]
    missing = [name for name, node in zip(node_names, nodes) if node is None]
    if missing:
        return _invalid("nodes not found: {}".format(", ".join(missing)))

    by_name = {node.name(): node for node in nodes}
    for item in exposed:
        target_node = by_name.get(item.get("target_node"))
        if target_node is None or target_node.knob(item.get("target_knob")) is None:
            return _invalid(
                "exposed knob target not found: {}.{}".format(item.get("target_node"), item.get("target_knob"))
            )

    for node in nuke.allNodes(recurseGroups=False):
        node.setSelected(False)
    for node in nodes:
        node.setSelected(True)

    group = nuke.collapseToGroup(show=False)
    group.setName(gizmo_name)
    for item in exposed:
        link = nuke.Link_Knob(item["name"], item.get("label") or item["name"])
        group.addKnob(link)
        link.makeLink(item["target_node"], item["target_knob"])

    manifest = {
        "name": gizmo_name,
        "version": version,
        "exposed_knobs": exposed_names,
    }
    metadata = nuke.String_Knob("dcc_mcp_asset_manifest", "DCC MCP Asset Manifest")
    metadata.setValue(json.dumps(manifest, separators=(",", ":"), sort_keys=True))
    metadata.setFlag(nuke.INVISIBLE)
    group.addKnob(metadata)

    for node in nuke.allNodes(recurseGroups=False):
        node.setSelected(False)
    group.setSelected(True)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(target)
    temporary.unlink(missing_ok=True)
    try:
        nuke.nodeCopy(str(temporary))
        if not temporary.is_file():
            return skill_error("Failed to package Gizmo", "Nuke did not serialize the Group")
        _normalize_gizmo(temporary)
        _publish_file(temporary, target, overwrite=overwrite)
    except (OSError, ValueError) as exc:
        return skill_error("Failed to package Gizmo", str(exc))
    finally:
        temporary.unlink(missing_ok=True)

    return skill_success(
        "Packaged Nuke Gizmo",
        gizmo_name=gizmo_name,
        group_node=group.name(),
        output_path=str(target),
        version=version,
        node_count=len(nodes),
        exposed_knobs=exposed_names,
    )
