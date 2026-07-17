from pathlib import Path

from dcc_mcp_core.skill import skill_entry, skill_error, skill_success


@skill_entry
def main(path: str, discard_unsaved_changes: bool = False, **_kwargs):
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        return skill_error("Invalid Nuke script path", "path must be absolute")

    target = candidate.resolve()
    if target.suffix.lower() != ".nk":
        return skill_error("Invalid Nuke script path", "script path must end in .nk")
    if not target.exists():
        return skill_error("Invalid Nuke script path", "script does not exist")
    if not target.is_file():
        return skill_error("Invalid Nuke script path", "script path is not a file")

    import nuke  # Lazy import: requires Nuke.

    if nuke.root().modified() and not discard_unsaved_changes:
        return skill_error(
            "Failed to open Nuke script",
            "current script has unsaved changes; set discard_unsaved_changes to true",
        )

    nuke.scriptClear()
    nuke.scriptOpen(str(target))
    opened = Path(nuke.scriptName()).expanduser().resolve()
    if opened != target:
        return skill_error("Failed to open Nuke script", "opened path does not match requested script")

    root = nuke.root()
    nodes = nuke.allNodes(recurseGroups=True)
    return skill_success(
        "Opened Nuke script",
        path=str(opened),
        first_frame=root["first_frame"].value(),
        last_frame=root["last_frame"].value(),
        node_count=len(nodes),
    )
