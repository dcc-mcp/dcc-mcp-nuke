from dcc_mcp_core.skill import skill_entry

from dcc_mcp_nuke.image_statistics import sample_channel_statistics


@skill_entry
def main(**kwargs):
    return sample_channel_statistics(**kwargs)
