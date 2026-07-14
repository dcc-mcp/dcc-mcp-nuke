from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_nuke.compositing import build_layered_comp


@skill_entry
def main(manifest, **_kwargs):
    import nuke

    result = build_layered_comp(nuke, manifest)
    return skill_success("Built layered Nuke composite", **result)
