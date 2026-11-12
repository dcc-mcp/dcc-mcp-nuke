from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_nuke.diagnostics import describe_execution_sources


@skill_entry
def main(**_kwargs):
    return skill_success("Described Nuke execution sources", **describe_execution_sources())
