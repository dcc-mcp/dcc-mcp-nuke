"""Render an explicit Nuke Write node and read back its real output files.

The readback is a filesystem check only; a returning ``nuke.execute`` is never
native Nuke render acceptance. The active Write filename comes from
``nuke.filename(node)`` (proxy-aware, sequence placeholders preserved), and
Write frame renumbering is resolved arithmetically where the Nuke knob
contract allows it. Unresolvable mappings stay explicitly unknown rather than
being checked against stale timeline-numbered files.
"""

from __future__ import annotations

import re
from typing import Any

from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.diagnostics import verify_render_output

# A Nuke Write node exposes ``frame_mode`` (expression / start at / offset) and
# a ``frame`` knob whose meaning depends on that mode. Only mappings resolvable
# with integer arithmetic are supported; arbitrary host expressions are never
# evaluated. See Foundry's "Changing the Numbering of Rendered Frames".
_IDENTITY_EXPRESSIONS = frozenset({"frame", "[frame]"})
_INTEGER = re.compile(r"^[+-]?\d+$")


def _unknown_verification(reason: str) -> dict[str, Any]:
    """Fail-closed readback shape shared by every unverifiable mapping."""
    return {
        "verified": False,
        "mode": "unknown",
        "content_validation": "not_performed",
        "content_acceptance": "unknown",
        "content_checks": {},
        "expected_frames": [],
        "warnings": [{"code": "output_verification_unknown", "detail": reason}],
        "reason": reason,
        "scope": "filesystem readback only; NOT native Nuke render acceptance.",
        "native_render_acceptance": "not_performed",
    }


def _frame_mode_name(node: Any) -> str | None:
    """Read the frame_mode enum as a normalized name, or None if unreadable.

    A real Nuke Enumeration_Knob.value() may return the name or an index;
    values()/enumName() map an index back to the name.
    """
    try:
        knob = node["frame_mode"]
        raw = knob.value()
    except Exception:
        return None
    if isinstance(raw, str):
        return raw.strip().lower() or None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        index = int(raw)
        try:
            names = knob.values()
        except Exception:
            names = None
        if isinstance(names, (list, tuple)) and 0 <= index < len(names):
            return str(names[index]).strip().lower() or None
        try:
            return str(knob.enumName(index)).strip().lower() or None
        except Exception:
            return None
    return None


def _integer_knob(node: Any, name: str) -> int | None:
    """Read a knob holding a whole number without evaluating host expressions."""
    try:
        raw = node[name].value()
    except Exception:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    if isinstance(raw, str) and _INTEGER.fullmatch(raw.strip()):
        return int(raw.strip())
    return None


def _output_frame_bounds(node: Any, first_frame: int, last_frame: int, step: int):
    """Return (out_first, out_last, mapping_kind), or None when unresolvable.

    ``offset`` adds a constant to every frame; ``start at`` renumbers the first
    rendered frame to ``start``. Both are constant shifts, so the whole range
    can be checked arithmetically. Any other expression, an unsupported mode or
    an unreadable knob stays unknown instead of guessing timeline filenames.
    """
    mode = _frame_mode_name(node)
    if mode == "expression":
        try:
            expression = node["frame"].value()
        except Exception:
            return None
        # Only literal identity markers are recognized, never evaluated.
        if isinstance(expression, str) and expression.strip().lower() in _IDENTITY_EXPRESSIONS:
            return first_frame, last_frame, "identity"
        return None
    if mode == "offset":
        offset = _integer_knob(node, "frame")
        if offset is None:
            return None
        return first_frame + offset, last_frame + offset, f"offset:{offset}"
    if mode == "start at" and step == 1:
        start = _integer_knob(node, "frame")
        if start is None:
            return None
        return start, start + (last_frame - first_frame), f"start_at:{start}"
    return None


@skill_entry
def main(write_node: str, first_frame: int, last_frame: int, step: int = 1, **_kwargs):
    import nuke

    if last_frame < first_frame:
        return skill_error(
            "last_frame must be greater than or equal to first_frame",
            f"last_frame={last_frame} is less than first_frame={first_frame}",
        )
    if step < 1:
        return skill_error("step must be greater than or equal to 1", f"step={step} is less than 1")
    node = nuke.toNode(write_node)
    if node is None or node.Class() != "Write":
        return skill_error("Write node not found", write_node)
    first, last, incr = int(first_frame), int(last_frame), int(step)

    # nuke.filename(node) is the ACTIVE Write filename (proxy-aware) and keeps
    # sequence placeholders unless nuke.REPLACE is requested. The file knob is
    # not the output path when Root proxy mode selects the proxy filename.
    filename_api = getattr(nuke, "filename", None)
    try:
        output_path = filename_api(node) if callable(filename_api) else None
    except Exception:
        output_path = None

    nuke.execute(node, first, last, incr)
    # execute() returning is not proof that files exist. Report a separate,
    # explicit filesystem readback so a successful call cannot be mistaken for
    # verified output. This is not native Nuke render acceptance.
    mapping = _output_frame_bounds(node, first, last, incr)
    if output_path is None or mapping is None:
        reason = (
            "active Write filename could not be resolved"
            if output_path is None
            else "Write frame renumbering could not be resolved without evaluating host expressions"
        )
        verification = _unknown_verification(
            reason + "; output files were not checked. Verification stays unknown, not a render failure."
        )
    else:
        out_first, out_last, mapping_kind = mapping
        try:
            verification = verify_render_output(
                output_path=output_path,
                first_frame=out_first,
                last_frame=out_last,
                step=incr,
            )
        except (TypeError, ValueError) as exc:
            verification = _unknown_verification("output path could not be checked: " + str(exc)[:256])
        verification["output_frame_mapping"] = {
            "kind": mapping_kind,
            "timeline_first_frame": first,
            "timeline_last_frame": last,
            "step": incr,
            "first_output_frame": out_first,
            "last_output_frame": out_last,
        }
    return skill_success(
        "Rendered Nuke Write node",
        verified=verification.get("verified") is True,
        write_node=write_node,
        first_frame=first,
        last_frame=last,
        step=incr,
        output_path=output_path,
        output_verification=verification,
    )
