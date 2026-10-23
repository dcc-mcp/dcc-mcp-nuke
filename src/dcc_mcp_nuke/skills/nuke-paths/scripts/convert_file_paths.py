from __future__ import annotations

from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.file_references import FileReferenceError, convert_file_paths


@skill_entry
def main(
    mode: str,
    dry_run: bool = True,
    node_classes: list[str] | None = None,
    max_items: int = 256,
    **_kwargs,
):
    import nuke

    try:
        result = convert_file_paths(nuke, mode, dry_run=dry_run, node_classes=node_classes, max_items=max_items)
    except FileReferenceError as exc:
        return skill_error("Failed to convert Nuke file paths", str(exc))
    except Exception:
        return skill_error("Failed to convert Nuke file paths", "unexpected Nuke host failure")
    return skill_success("Previewed Nuke path conversion" if dry_run else "Converted Nuke file paths", **result)
