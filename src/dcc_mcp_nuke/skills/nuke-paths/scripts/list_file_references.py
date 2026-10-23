from __future__ import annotations

from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.file_references import FileReferenceError, list_file_references


@skill_entry
def main(max_items: int = 256, node_classes: list[str] | None = None, **_kwargs):
    import nuke

    try:
        result = list_file_references(nuke, max_items=max_items, node_classes=node_classes)
    except FileReferenceError as exc:
        return skill_error("Failed to inspect Nuke file references", str(exc))
    except Exception:
        return skill_error("Failed to inspect Nuke file references", "unexpected Nuke host failure")
    return skill_success("Inspected Nuke file references", **result)
