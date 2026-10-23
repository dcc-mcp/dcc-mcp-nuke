from __future__ import annotations

from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.file_references import FileReferenceError
from dcc_mcp_nuke.script_package import PackageScriptError, package_script


@skill_entry
def main(
    output_path: str,
    dry_run: bool = True,
    frame_mode: str = "all",
    first_frame: int | None = None,
    last_frame: int | None = None,
    max_references: int = 1024,
    max_files: int = 10000,
    max_total_bytes: int = 1000000000000,
    max_report_items: int = 256,
    **_kwargs,
):
    import nuke

    try:
        result = package_script(
            nuke,
            output_path,
            dry_run=dry_run,
            frame_mode=frame_mode,
            first_frame=first_frame,
            last_frame=last_frame,
            max_references=max_references,
            max_files=max_files,
            max_total_bytes=max_total_bytes,
            max_report_items=max_report_items,
        )
    except PackageScriptError as exc:
        return skill_error("Failed to collect Nuke script", str(exc), **exc.context)
    except FileReferenceError as exc:
        return skill_error("Failed to collect Nuke script", str(exc))
    except Exception:
        return skill_error("Failed to collect Nuke script", "unexpected host or filesystem failure")
    return skill_success("Previewed Nuke script collection" if dry_run else "Collected Nuke script", **result)
