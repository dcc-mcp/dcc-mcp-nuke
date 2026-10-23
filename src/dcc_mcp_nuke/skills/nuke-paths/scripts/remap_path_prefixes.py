from __future__ import annotations

from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.file_references import FileReferenceError, remap_path_prefixes


@skill_entry
def main(
    mappings: list[dict[str, str]],
    dry_run: bool = True,
    only_existing: bool = False,
    node_classes: list[str] | None = None,
    max_items: int = 256,
    **_kwargs,
):
    import nuke

    try:
        result = remap_path_prefixes(
            nuke,
            mappings,
            dry_run=dry_run,
            only_existing=only_existing,
            node_classes=node_classes,
            max_items=max_items,
        )
    except FileReferenceError as exc:
        return skill_error("Failed to remap Nuke file paths", str(exc))
    except Exception:
        return skill_error("Failed to remap Nuke file paths", "unexpected Nuke host failure")
    return skill_success("Previewed Nuke file remap" if dry_run else "Remapped Nuke file paths", **result)
