"""Render an explicit Nuke Write node and read back its real output files.

The readback is a filesystem check only; a returning ``nuke.execute`` is never
native Nuke render acceptance. The active Write filename comes from
``nuke.filename(node)`` (proxy-aware, sequence placeholders preserved) and is
read AFTER ``nuke.execute`` so a ``beforeRender`` callback that changes the
path is captured. Path-affecting callback state is snapshotted BEFORE and AFTER
execute and combined: a callback that was set before the render but cleared
itself during it cannot be ruled out by a post-only check. Write frame
renumbering is resolved arithmetically only where the Nuke knob contract allows
it; ``start at`` depends on the first frame of the clip/sequence (not the first
requested frame), which cannot be proven from the Write node alone, so it stays
explicitly unknown. The output format is bound to the Write ``file_type`` or a
well-founded path extension before any file is accepted: a readable, empty
``file_type`` permits path inference, while an unreadable ``file_type`` stays
unknown because the real value could override the path suffix. Unresolvable
mappings, callback-driven path sets and unknown formats stay unknown rather
than being checked against stale or wrong-format files.
"""

from __future__ import annotations

import re
from typing import Any

from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

from dcc_mcp_nuke.diagnostics import resolve_output_format, verify_render_output

# A Nuke Write node exposes ``frame_mode`` (expression / start at / offset) and
# a ``frame`` knob whose meaning depends on that mode. Only mappings resolvable
# with integer arithmetic are supported; arbitrary host expressions are never
# evaluated. See Foundry's "Changing the Numbering of Rendered Frames".
_IDENTITY_EXPRESSIONS = frozenset({"frame", "[frame]"})
_INTEGER = re.compile(r"^[+-]?\d+$")

# Write node Python Tab callbacks (Foundry Write node reference). A
# ``beforeRender`` script runs once before rendering starts inside execute(),
# so reading the active filename afterwards captures its single changed path.
# Frame-level and post-render scripts can change the path per frame or after
# the render, so one final filename cannot prove the whole output set.
_RENDER_CALLBACK_KNOBS = ("beforeRender", "beforeFrameRender", "afterFrameRender", "afterRender")
_MULTI_PATH_CALLBACK_KNOBS = ("beforeFrameRender", "afterFrameRender", "afterRender")


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


def _enum_knob_name(node: Any, name: str) -> str | None:
    """Read an Enumeration_Knob as a normalized name, or None if unreadable.

    A real Nuke Enumeration_Knob.value() may return the name or an index;
    values()/enumName() map an index back to the name.
    """
    try:
        knob = node[name]
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


def _frame_mode_name(node: Any) -> str | None:
    """Normalized ``frame_mode`` name, or None if the knob is unreadable."""
    return _enum_knob_name(node, "frame_mode")


def _file_type_name(node: Any) -> str | None:
    """Read the Write ``file_type`` with a three-way result:

    * a non-empty ``str`` -> explicit, normalized name (may be unsupported);
    * ``""`` -> readable and genuinely empty, so path-extension inference is a
      proven, legal binding;
    * ``None`` -> UNREADABLE/unknown (read failed or value could not be
      resolved), which is NOT the same as empty: the real value could override
      the path suffix, so the caller must keep verification unknown instead of
      inferring a format from the filename.

    ``_enum_knob_name`` is intentionally not reused: it returns None for both
    an empty value and a read failure, which would turn an unreadable
    ``file_type`` into a path-inferred (unproven) format binding.
    """
    try:
        knob = node["file_type"]
        raw = knob.value()
    except Exception:
        return None
    if raw is None:
        return ""  # readable, no explicit format -> empty
    if isinstance(raw, str):
        return raw.strip().lower()  # "" -> empty, otherwise an explicit name
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        index = int(raw)
        try:
            names = knob.values()
        except Exception:
            names = None
        if isinstance(names, (list, tuple)) and 0 <= index < len(names):
            return str(names[index]).strip().lower()
        try:
            return str(knob.enumName(index)).strip().lower()
        except Exception:
            return None  # unresolvable index -> unknown
    return None  # unexpected knob shape -> unknown


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


def _output_frame_bounds(node: Any, first_frame: int, last_frame: int):
    """Return (out_first, out_last, mapping_kind), or None when unresolvable.

    ``offset`` adds a constant to every frame, so the whole range can be
    checked arithmetically. ``start at`` renumbers against the first frame of
    the clip/sequence, which is NOT the first requested frame and cannot be
    proven from the Write node alone (Foundry "Changing the Numbering of
    Rendered Frames"); substituting ``first_frame`` for that origin would make
    a partial-range render verify stale output-N files. ``start at`` therefore
    stays unknown. Any other expression, an unsupported mode or an unreadable
    knob also stays unknown instead of guessing timeline filenames.
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
    return None


def _callback_is_set(node: Any, name: str) -> bool:
    """Report whether a non-empty callback script is present.

    The script is only inspected, never executed or parsed. A read failure or
    an unexpected knob shape propagates so the caller fails closed rather than
    assuming "no callbacks".
    """
    raw = node[name].value()
    if raw is None:
        return False
    if not isinstance(raw, str):
        raise TypeError("unexpected callback knob value")
    return bool(raw.strip())


def _callback_state_snapshot(node: Any) -> dict[str, bool] | None:
    """Read the present/absent state of every render callback, or None if any
    knob is unreadable. Scripts are inspected, never executed or parsed. The
    caller fails closed when the snapshot is None instead of assuming "no
    callbacks".
    """
    try:
        return {name: _callback_is_set(node, name) for name in _RENDER_CALLBACK_KNOBS}
    except Exception:
        return None


def _active_output_path_after_execute(
    nuke: Any, node: Any, pre_callback_state: dict[str, bool] | None
) -> tuple[str | None, str | None]:
    """Resolve the active Write filename AFTER ``nuke.execute`` returned.

    ``pre_callback_state`` is the callback snapshot taken BEFORE execute. A
    callback that was set before the render but cleared itself during it (e.g.
    an ``afterRender`` script repointing ``file`` and clearing its own knob)
    must not be lost: path-affecting callbacks present on EITHER side, or an
    unreadable snapshot on EITHER side, keep verification unknown.
    ``beforeRender`` makes at most one path change, which the post-execute
    active filename captures, so its presence alone does not force unknown.
    Render callback scripts are inspected but never executed or parsed.
    """
    post_callback_state = _callback_state_snapshot(node)
    if pre_callback_state is None or post_callback_state is None:
        return None, "Write render callback state could not be read; a single final output path cannot be proven"
    if any(pre_callback_state[name] or post_callback_state[name] for name in _MULTI_PATH_CALLBACK_KNOBS):
        return None, (
            "a per-frame or afterRender callback present before or after execute can change the output path; "
            "one final filename cannot prove the rendered output set"
        )
    # beforeRender runs once before rendering starts; reading the active
    # filename now captures its single changed path.
    filename_api = getattr(nuke, "filename", None)
    if not callable(filename_api):
        return None, "nuke.filename(node) is unavailable after execute"
    try:
        output_path = filename_api(node)
    except Exception:
        return None, "active Write filename could not be read after execute"
    if not isinstance(output_path, str) or not output_path:
        return None, "active Write filename could not be resolved after execute"
    return output_path, None


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

    # Snapshot callback state BEFORE execute: a callback present now but that
    # clears itself during the render cannot be ruled out by a post-only read.
    pre_callback_state = _callback_state_snapshot(node)
    nuke.execute(node, first, last, incr)
    # execute() returning is not proof that files exist. Report a separate,
    # explicit filesystem readback so a successful call cannot be mistaken for
    # verified output. This is not native Nuke render acceptance.
    output_path, path_reason = _active_output_path_after_execute(nuke, node, pre_callback_state)
    mapping = _output_frame_bounds(node, first, last)
    observed_file_type = _file_type_name(node)
    # None means the file_type knob is UNREADABLE, not empty: the real value could
    # override the path suffix, so it must not reach resolve_output_format (which
    # would infer an unproven format from the path). A readable ""/None value is
    # empty and legitimately allows path-extension inference.
    file_type_unreadable = observed_file_type is None
    if output_path is not None and not file_type_unreadable:
        expected_format = resolve_output_format(observed_file_type, output_path)
    else:
        expected_format = None

    if path_reason is not None:
        reason = path_reason
    elif file_type_unreadable:
        reason = (
            "the Write file_type knob could not be read; the real output format cannot be bound, "
            "so verification stays unknown instead of inferring a format from the path"
        )
    elif mapping is None:
        reason = (
            "Write frame renumbering could not be resolved without evaluating host expressions "
            "or proving the clip/sequence origin"
        )
    elif expected_format is None:
        reason = "Write output format could not be bound; verification stays unknown, not unrestricted format sniffing"
    else:
        reason = None

    if reason is not None:
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
                expected_format=expected_format,
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
