from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.diagnostics import diagnose_dependencies


@skill_entry
def main(required_modules=None, required_node_classes=None, max_items=128, **_kwargs):
    try:
        import nuke
    except ImportError:
        nuke = None
    try:
        result = diagnose_dependencies(
            nuke,
            required_modules=required_modules,
            required_node_classes=required_node_classes,
            max_items=max_items,
        )
    except ValueError as exc:
        return skill_error("Invalid dependency diagnosis request", str(exc))
    except Exception:
        return skill_error(
            "Dependency diagnosis could not be completed",
            "The read-only probe failed; no conclusion about the missing modules or nodes is drawn.",
        )
    return skill_success("Diagnosed Nuke dependency state", **result)
