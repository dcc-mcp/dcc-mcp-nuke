import json
import re
from pathlib import Path

from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _invalid(error):
    return skill_error("Invalid Gizmo instance request", error)


@skill_entry
def main(gizmo_path, node_name=None, knob_values=None, **_kwargs):
    candidate = Path(gizmo_path).expanduser()
    if not candidate.is_absolute():
        return _invalid("gizmo_path must be absolute")
    target = candidate.resolve()
    if target.suffix.lower() != ".gizmo":
        return _invalid("gizmo_path must end in .gizmo")
    if not target.is_file():
        return _invalid("gizmo_path must be an existing file")

    asset_name = target.stem
    if not _IDENTIFIER.fullmatch(asset_name):
        return _invalid("Gizmo filename must be a Nuke identifier")
    if node_name is not None and not _IDENTIFIER.fullmatch(node_name):
        return _invalid("node_name must be a Nuke identifier")
    values = dict(knob_values or {})
    if any(not isinstance(name, str) or not name for name in values):
        return _invalid("knob_values keys must be non-empty strings")

    import nuke  # Lazy import: requires Nuke.

    node = None
    try:
        nuke.pluginAddPath(str(target.parent))
        nuke.load(asset_name)
        node = nuke.createNode(asset_name, inpanel=False)
        if node_name:
            node.setName(node_name)

        missing = [name for name in values if node.knob(name) is None]
        if missing:
            nuke.delete(node)
            return _invalid("Gizmo knobs not found: {}".format(", ".join(missing)))
        for name, value in values.items():
            node.knob(name).setValue(value)

        manifest = {}
        manifest_knob = node.knob("dcc_mcp_asset_manifest")
        if manifest_knob is not None:
            manifest = json.loads(manifest_knob.value())
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        if node is not None:
            try:
                nuke.delete(node)
            except Exception:  # Nuke may already have removed a failed node.
                pass
        return skill_error("Failed to instantiate Gizmo", str(exc))

    return skill_success(
        "Instantiated Nuke Gizmo",
        gizmo_path=str(target),
        asset_name=manifest.get("name", asset_name),
        node_name=node.name(),
        node_class=node.Class(),
        version=manifest.get("version"),
        applied_knobs=sorted(values),
    )
