from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.machine_learning import MachineLearningError, train_ml_node


@skill_entry
def main(node_name, output_directory, max_runtime_seconds=60, **_kwargs):
    import nuke

    try:
        result = train_ml_node(nuke, node_name, output_directory, max_runtime_seconds=max_runtime_seconds)
    except MachineLearningError as exc:
        return skill_error(str(exc), exc.code, **exc.context)
    return skill_success("Completed bounded Nuke AIR training", **result)
