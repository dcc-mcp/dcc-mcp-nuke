from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.text_layout import TextLayoutError, upsert_text2_label


@skill_entry
def main(
    node_name: str,
    text: str,
    font_size_px: float,
    x: int,
    y: int,
    box_x: float,
    box_y: float,
    box_width: float,
    box_height: float,
    horizontal_justify: str = "left",
    vertical_justify: str = "baseline",
    **_kwargs,
):
    import nuke

    try:
        result = upsert_text2_label(
            nuke,
            node_name=node_name,
            text=text,
            font_size_px=font_size_px,
            x=x,
            y=y,
            box_x=box_x,
            box_y=box_y,
            box_width=box_width,
            box_height=box_height,
            horizontal_justify=horizontal_justify,
            vertical_justify=vertical_justify,
        )
    except TextLayoutError as exc:
        return skill_error("Failed to upsert Nuke Text2 label", str(exc))
    except Exception:
        return skill_error("Failed to upsert Nuke Text2 label", "unexpected Nuke host failure")
    return skill_success("Upserted Nuke Text2 label", **result)
