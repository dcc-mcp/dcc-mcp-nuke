from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.file_references import FileReferenceError
from dcc_mcp_nuke.script_package import inspect_package


@skill_entry
def main(output_path: str, max_report_items: int = 256, **_kwargs):
    try:
        result = inspect_package(output_path, max_report_items=max_report_items)
    except FileReferenceError as exc:
        return skill_error("Failed to inspect Nuke script package", str(exc))
    except Exception:
        return skill_error("Failed to inspect Nuke script package", "unexpected filesystem failure")
    return skill_success("Inspected Nuke script package", **result)
