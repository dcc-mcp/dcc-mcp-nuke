from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.machine_learning import MachineLearningError, inspect_ml_environment


@skill_entry
def main(model_directories=None, node_name=None, max_items=128, max_scan_entries=4096, **_kwargs):
    import nuke

    try:
        result = inspect_ml_environment(
            nuke,
            model_directories=model_directories,
            node_name=node_name,
            max_items=max_items,
            max_scan_entries=max_scan_entries,
        )
    except MachineLearningError as exc:
        return skill_error(str(exc), exc.code, **exc.context)
    return skill_success("Inspected Nuke AIR readiness", **result)
