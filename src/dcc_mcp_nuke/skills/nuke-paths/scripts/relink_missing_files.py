from __future__ import annotations

from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.file_references import FileReferenceError, relink_missing_files


@skill_entry
def main(
    search_folders: list[str],
    dry_run: bool = True,
    node_classes: list[str] | None = None,
    max_items: int = 256,
    max_search_files: int = 10000,
    **_kwargs,
):
    import nuke

    try:
        result = relink_missing_files(
            nuke,
            search_folders,
            dry_run=dry_run,
            node_classes=node_classes,
            max_items=max_items,
            max_search_files=max_search_files,
        )
    except FileReferenceError as exc:
        return skill_error("Failed to relink Nuke file paths", str(exc))
    except Exception:
        return skill_error("Failed to relink Nuke file paths", "unexpected Nuke host failure")
    return skill_success("Previewed Nuke missing-file relink" if dry_run else "Relinked Nuke missing files", **result)
