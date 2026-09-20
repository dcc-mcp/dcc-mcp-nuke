"""Read-only Nuke Studio timeline surface inspection.

Skeleton for the Studio-only capability surface. It reports what the live
Hiero API actually exposes and says so explicitly when a probe is missing:
a Studio build whose API differs is reported, never silently skipped.
"""

from __future__ import annotations

from typing import Any, Optional

from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.host_flavor import FLAVOR_NUKE_STUDIO, detect_host_flavor, missing_capability_error

_TOOL_NAME = "inspect_timeline"
_SKILL_NAME = "nuke-studio-timeline"


def _call(target: Any, name: str) -> Optional[Any]:
    """Invoke ``target.name()`` when it exists, else return ``None``."""
    method = getattr(target, name, None)
    if not callable(method):
        return None
    try:
        return method()
    except Exception:
        return None


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sequence_summary(sequence: Any) -> dict:
    video = _call(sequence, "videoTracks") or ()
    audio = _call(sequence, "audioTracks") or ()
    return {
        "name": str(_call(sequence, "name") or ""),
        "first_frame": _as_int(_call(sequence, "inTime")),
        "last_frame": _as_int(_call(sequence, "outTime")),
        "video_track_count": len(video) if hasattr(video, "__len__") else None,
        "audio_track_count": len(audio) if hasattr(audio, "__len__") else None,
    }


def _project_summary(project: Any, max_sequences: int) -> tuple:
    """Return ``(summary, reason)`` for one Hiero project."""
    sequences_getter = getattr(project, "sequences", None)
    sequences: list = []
    reason = ""
    if callable(sequences_getter):
        try:
            sequences = list(sequences_getter() or ())
        except Exception as exc:
            reason = f"sequence enumeration failed: {type(exc).__name__}"
    else:
        reason = "hiero project does not expose sequences()"
    summary = {
        "name": str(_call(project, "name") or ""),
        "sequence_count": len(sequences),
        "sequences_truncated": len(sequences) > max_sequences,
        "sequences": [_sequence_summary(item) for item in sequences[:max_sequences]],
    }
    return summary, reason


@skill_entry
def main(max_sequences: int = 20, **_kwargs) -> dict:
    """Report the Nuke Studio timeline surface, or why it is unavailable."""
    if not 1 <= max_sequences <= 100:
        return skill_error("max_sequences must be between 1 and 100", "invalid_range")

    report = detect_host_flavor()
    if report.flavor != FLAVOR_NUKE_STUDIO:
        return missing_capability_error(
            tool_name=_TOOL_NAME,
            flavor=report.flavor,
            required=[FLAVOR_NUKE_STUDIO],
            skill_name=_SKILL_NAME,
        )

    try:
        import hiero.core as hiero_core
    except Exception as exc:
        return skill_error(
            "Nuke Studio timeline surface is unavailable",
            "studio_surface_unavailable",
            prompt="Run this tool inside Nuke Studio with the Hiero Python package importable.",
            possible_solutions=[
                "Launch Nuke Studio rather than Nuke or NukeX.",
                "Re-run dcc-mcp-nuke verify against the Nuke Studio executable.",
            ],
            host_flavor=report.flavor,
            read_only=True,
            studio_surface=False,
            reason=f"hiero.core import failed: {type(exc).__name__}",
        )

    projects_getter = getattr(hiero_core, "projects", None)
    if not callable(projects_getter):
        return skill_error(
            "Nuke Studio timeline surface is unavailable",
            "studio_surface_unavailable",
            prompt="This Hiero build does not expose hiero.core.projects().",
            host_flavor=report.flavor,
            read_only=True,
            studio_surface=False,
            reason="hiero.core.projects() is unavailable",
        )

    try:
        projects = list(projects_getter() or ())
    except Exception as exc:
        return skill_error(
            "Nuke Studio timeline surface is unavailable",
            "studio_surface_unavailable",
            prompt="Hiero rejected the project enumeration call.",
            host_flavor=report.flavor,
            read_only=True,
            studio_surface=False,
            reason=f"projects() failed: {type(exc).__name__}",
        )

    summaries = []
    reasons = []
    for project in projects:
        summary, reason = _project_summary(project, max_sequences)
        summaries.append(summary)
        if reason:
            reasons.append(f"{summary['name'] or '<unnamed>'}: {reason}")

    return skill_success(
        f"Inspected {len(summaries)} Nuke Studio project(s)",
        read_only=True,
        host_flavor=report.flavor,
        studio_surface=True,
        project_count=len(summaries),
        projects=summaries,
        reason="; ".join(reasons),
    )
