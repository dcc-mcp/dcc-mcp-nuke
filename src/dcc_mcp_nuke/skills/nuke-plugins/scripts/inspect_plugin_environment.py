from __future__ import annotations

from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.plugins import inspect_plugin_environment


@skill_entry
def main(
    required_node_classes: list[str] | None = None,
    task_roots: list[str] | None = None,
    name_filter: str = "",
    max_items: int = 128,
    max_scan_entries: int = 4096,
    **_kwargs,
):
    import nuke

    try:
        result = inspect_plugin_environment(
            nuke,
            required_node_classes=required_node_classes,
            task_roots=task_roots,
            name_filter=name_filter,
            max_items=max_items,
            max_scan_entries=max_scan_entries,
        )
    except ValueError as exc:
        return skill_error("Invalid Nuke plug-in inventory request", str(exc))
    except Exception:
        return skill_error("Failed to inspect Nuke plug-in environment", "unexpected host or filesystem failure")
    return skill_success("Inspected Nuke plug-in environment", **result)
