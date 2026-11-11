"""Render an explicit Nuke Write node and report an honest readback verdict.

The normal render action is ``nuke.execute``; it still runs every configured
render callback (Write-node scripts and process-wide ``nuke.add*`` handlers
registered in ``init.py``/``menu.py``). A returning ``nuke.execute`` is never
native Nuke render acceptance.

The wrapper does NOT claim to verify host-derived output. Nuke exposes no
public API to enumerate process-wide render callbacks, to observe a callback
registered/removed during ``execute()``, or to obtain a receipt of the path(s)
actually written. A callback can repoint the Write ``file`` knob during the
render and restore the original template afterwards: all four Write-node
callback knobs then stay empty, a single post-execute ``nuke.filename`` is
unchanged, and the raw ``file`` knob is equal before and after ``execute()``.
That change-and-restore case is observationally identical to a clean render
from EVERY public-API observation, so endpoint comparison, empty before/after
knob snapshots and one final filename cannot prove the complete output path
set. The readback therefore fails closed to ``unknown`` for EVERY render,
never a false ``verified=true`` on a stale or wrong file. This is a host
limitation, not a render failure.

The active Write filename is still read once AFTER ``execute`` (proxy-aware,
sequence placeholders preserved) as best-effort, INFORMATIONAL context; it is
never used to claim verification. The genuinely provable, independent file
verification contract is ``dcc_mcp_nuke.diagnostics.verify_render_output``:
it inspects an explicit, caller-supplied output path and is deliberately NOT
used here to bless a host-derived single filename. The wrapper never executes
or parses callback scripts; the render invoked through ``nuke.execute`` still
runs configured callbacks.
"""

from __future__ import annotations

import re
from typing import Any

from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

# The genuinely provable, independent filesystem verification contract lives in
# dcc_mcp_nuke.diagnostics.verify_render_output and needs an explicit,
# caller-supplied output path. It is deliberately NOT imported here: a
# host-derived single filename can never satisfy that contract's precondition.
from dcc_mcp_nuke.diagnostics import resolve_output_format

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

# Nuke exposes no public API to enumerate process-wide render callbacks
# (nuke.add*), to observe a handler registered/removed during execute(), or to
# obtain a receipt of the path(s) actually written. A callback can repoint the
# Write ``file`` knob during the render and restore it afterwards, leaving all
# four node callback knobs empty and EQUAL pre/post raw ``file`` endpoints.
# A clean render and that change-and-restore case are indistinguishable from
# every public-API observation, so the host-derived readback always fails
# closed to unknown instead of verifying one final filename.
_UNREADABLE = object()
_UNPROVABLE_CALLBACK_CONTEXT = (
    "the complete rendered output path set cannot be proven from the host: "
    "globally registered render callbacks (nuke.add*) and temporary callbacks "
    "registered during execute are not enumerable and no actual-path receipt "
    "is available, so empty Write callback knobs and equal pre/post file knob "
    "endpoints cannot rule out a callback that repointed the active file path "
    "during execute and restored it afterwards; one final filename is never "
    "proof of the rendered output set"
)


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


def _write_file_knob(node: Any) -> Any:
    """Read the Write ``file`` knob as a read-only, frame-independent baseline.

    ``nuke.filename(node)`` is the active (possibly proxy) output path and may
    resolve sequence placeholders against the current frame, so it is not a
    stable baseline across ``execute()``. The raw ``file`` knob holds the path
    template and lets the wrapper detect a callback that repointed the output
    during the render without executing, parsing or hooking any script. A read
    failure returns a sentinel so the caller fails closed to ``unknown``.
    """
    try:
        return node["file"].value()
    except Exception:
        return _UNREADABLE


def _active_output_path_after_execute(
    nuke: Any, node: Any, pre_callback_state: dict[str, bool] | None, pre_file_knob: Any
) -> tuple[str | None, str | None]:
    """Return the best-effort active Write filename AFTER ``nuke.execute``.

    The returned path is INFORMATIONAL only and is NEVER used to claim a
    verified render. ``pre_callback_state`` (snapshotted BEFORE execute) and
    ``pre_file_knob`` (raw ``file`` knob baseline) are retained as fail-closed
    diagnostics: an unreadable callback snapshot or file knob, or a
    frame-level/``afterRender`` callback on either side, short-circuits the
    read. But even when every observation is clean and the raw ``file`` knob
    is EQUAL before and after execute, a process-wide ``nuke.add*`` callback
    (or a temporary handler registered during execute) could have repointed
    ``file`` during the render and restored it afterwards. Nuke exposes no
    public API to enumerate such handlers or obtain an actual-path receipt,
    so this case is indistinguishable from a clean render and EVERY branch
    here returns a non-None reason: the caller always fails closed to
    ``unknown``. Render callback scripts are inspected but never executed or
    parsed.
    """
    post_callback_state = _callback_state_snapshot(node)
    if pre_callback_state is None or post_callback_state is None:
        return None, "Write render callback state could not be read; a single final output path cannot be proven"
    if any(pre_callback_state[name] or post_callback_state[name] for name in _MULTI_PATH_CALLBACK_KNOBS):
        return None, (
            "a per-frame or afterRender callback present before or after execute can change the output path; "
            "one final filename cannot prove the rendered output set"
        )
    filename_api = getattr(nuke, "filename", None)
    if not callable(filename_api):
        return None, "nuke.filename(node) is unavailable after execute"
    try:
        output_path = filename_api(node)
    except Exception:
        return None, "active Write filename could not be read after execute"
    if not isinstance(output_path, str) or not output_path:
        return None, "active Write filename could not be resolved after execute"

    post_file_knob = _write_file_knob(node)
    if pre_file_knob is _UNREADABLE or post_file_knob is _UNREADABLE:
        return output_path, _UNPROVABLE_CALLBACK_CONTEXT
    # Equal, readable pre/post ``file`` knobs are NOT proof: a process-wide
    # callback can repoint ``file`` during execute and restore it afterwards,
    # and Nuke offers no way to enumerate such handlers or obtain an
    # actual-path receipt. Both this branch and the unreadable-baseline branch
    # intentionally return the same fail-closed verdict -- NO host-derived
    # observation here is sufficient to verify one final filename.
    return output_path, _UNPROVABLE_CALLBACK_CONTEXT


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

    # Snapshot callback state and the raw ``file`` knob BEFORE execute. These
    # are DIAGNOSTIC fail-closed signals only: a callback present now but that
    # clears itself (or repoints ``file`` and restores it) during the render
    # cannot be ruled out by a post-only read, and NO host observation can prove
    # the output path set. They never produce a positive verdict.
    pre_callback_state = _callback_state_snapshot(node)
    pre_file_knob = _write_file_knob(node)
    # The normal render action still runs every configured callback.
    nuke.execute(node, first, last, incr)
    # A best-effort, INFORMATIONAL active filename; NEVER a verification basis.
    output_path, path_reason = _active_output_path_after_execute(nuke, node, pre_callback_state, pre_file_knob)
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

    # Every host-derived readback fails closed. The inspections above are
    # DIAGNOSTIC ONLY: they populate ``reason`` so an operator can see what else
    # was unprovable, and NEVER produce a positive verdict. There is no
    # verification branch here: the independent verify_render_output contract
    # requires an explicit caller-supplied path and is deliberately not used to
    # bless a host-derived single filename.
    reasons = [_UNPROVABLE_CALLBACK_CONTEXT]
    if path_reason is not None and path_reason != _UNPROVABLE_CALLBACK_CONTEXT:
        reasons.append(path_reason)
    if file_type_unreadable:
        reasons.append(
            "the Write file_type knob could not be read; the real output format cannot be bound, "
            "so verification stays unknown instead of inferring a format from the path"
        )
    if mapping is None:
        reasons.append(
            "Write frame renumbering could not be resolved without evaluating host expressions "
            "or proving the clip/sequence origin"
        )
    if output_path is not None and not file_type_unreadable and expected_format is None:
        reasons.append(
            "Write output format could not be bound; verification stays unknown, not unrestricted format sniffing"
        )
    reason = "; ".join(reasons) + ("; output files were not checked. Verification stays unknown, not a render failure.")
    verification = _unknown_verification(reason)
    return skill_success(
        "Rendered Nuke Write node",
        verified=False,
        write_node=write_node,
        first_frame=first,
        last_frame=last,
        step=incr,
        output_path=output_path,
        output_verification=verification,
    )
