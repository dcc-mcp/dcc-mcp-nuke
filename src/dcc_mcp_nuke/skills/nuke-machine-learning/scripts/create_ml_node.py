from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.machine_learning import MachineLearningError, create_ml_node


@skill_entry
def main(node_class, name=None, input_node=None, reference_node=None, settings=None, **_kwargs):
    import nuke

    try:
        result = create_ml_node(
            nuke, node_class, name=name, input_node=input_node, reference_node=reference_node, settings=settings
        )
    except MachineLearningError as exc:
        return skill_error(str(exc), exc.code, **exc.context)
    return skill_success("Created Nuke AIR node", **result)
