"""Readback contracts for the render_write_node tool wrapper.

Every Nuke host here is an in-process fake (ModuleType + fake knobs). These
tests exercise the wrapper's readback logic only; they are NOT native Nuke
render acceptance and prove nothing about a real Nuke render.
"""

import base64
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# A genuinely decodable 2x2 RGB PNG (IHDR + IDAT + IEND, valid CRCs).
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR42mO4Iycnd0eOAUIBACAqBGGFHUPdAAAAAElFTkSuQmCC"
)
# A JPEG signature (no decoder here; signature-only, content stays unknown).
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 40


class _Knob:
    """Minimal fake of a Nuke Write knob, including the enum-name API.

    Real Nuke Enumeration_Knob.value() returns an index; the string name is
    obtained from values()/enumName(). This fake models both shapes.
    """

    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value

    def values(self):
        return ["expression", "start at", "offset"]

    def enumName(self, index):
        return self.values()[index]


def _fake_nuke(
    monkeypatch,
    file_path,
    *,
    active_path=None,
    frame_mode="expression",
    frame="frame",
    file_type=None,
    unreadable_knob=None,
):
    """Install a fake Nuke host whose active filename may differ from `file`.

    ``unreadable_knob`` makes one knob raise on access, modeling a knob whose
    value cannot be read at all (distinct from a readable, empty value).
    """
    values = {"file": file_path, "frame_mode": frame_mode, "frame": frame, "file_type": file_type}
    write = MagicMock()
    write.Class.return_value = "Write"

    def _get_knob(name):
        if name == unreadable_knob:
            raise RuntimeError(f"{name} knob unreadable")
        return _Knob(values.get(name))

    write.__getitem__.side_effect = _get_knob
    nuke = ModuleType("nuke")
    nuke.toNode = MagicMock(return_value=write)
    nuke.execute = MagicMock()
    nuke.filename = MagicMock(return_value=file_path if active_path is None else active_path)
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    return nuke, write


def _callback_nuke(monkeypatch, initial_path, new_path, *, frame_callback="", read_error_knob=None):
    """Fake Nuke whose ``nuke.filename(node)`` reflects the live ``file`` knob
    and whose ``execute()`` models a ``beforeRender`` callback changing that
    knob. In-process fake only; NOT native Nuke render acceptance.
    """
    values = {
        "file": initial_path,
        "frame_mode": "expression",
        "frame": "frame",
        "file_type": "png",
        "beforeRender": "change the active output path",
        "beforeFrameRender": frame_callback,
        "afterFrameRender": "",
        "afterRender": "",
    }

    class Knob:
        def __init__(self, value):
            self._value = value

        def value(self):
            return self._value

    class Write:
        def Class(self):
            return "Write"

        def __getitem__(self, name):
            if name == read_error_knob:
                raise RuntimeError("callback knob unreadable")
            return Knob(values.get(name))

    write = Write()

    def filename(_node):
        return values["file"]  # ACTIVE file knob, read live

    def execute(_node, _first, _last, _step):
        # Model beforeRender running inside execute() and changing the path.
        values["file"] = new_path

    nuke = ModuleType("nuke")
    nuke.toNode = lambda _name: write
    nuke.filename = filename
    nuke.execute = execute
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    return nuke, write


def _self_clearing_callback_nuke(monkeypatch, actual_path, stale_path):
    """Fake Nuke modeling an ``afterRender`` callback that is set BEFORE
    execute, then repoints ``file`` to a stale pre-existing output and clears
    its own knob DURING execute. A post-only callback snapshot would see no
    path-affecting callback and (wrongly) verify the stale file.

    In-process fake only; NOT native Nuke render acceptance. No host is
    launched and no user callback is executed or parsed: the script text is
    merely a non-empty marker that is inspected.
    """
    values = {
        "file": actual_path,
        "frame_mode": "expression",
        "frame": "frame",
        "file_type": "png",
        "beforeRender": "",
        "beforeFrameRender": "",
        "afterFrameRender": "",
        "afterRender": "repoint file to a stale output, then clear this callback",
    }

    class Knob:
        def __init__(self, value):
            self._value = value

        def value(self):
            return self._value

    class Write:
        def Class(self):
            return "Write"

        def __getitem__(self, name):
            return Knob(values.get(name))

    write = Write()

    def filename(_node):
        return values["file"]  # ACTIVE file knob, read live

    def execute(_node, first, last, _step):
        # The real render produced the actual frames ...
        for frame in range(first, last + 1):
            Path(str(actual_path).replace("%04d", f"{frame:04d}")).write_bytes(_PNG)
        # ... then the afterRender callback changed `file` to an existing stale
        # output and cleared its own script. This is the self-clearing case.
        values["file"] = stale_path
        values["afterRender"] = ""

    nuke = ModuleType("nuke")
    nuke.toNode = lambda _name: write
    nuke.filename = filename
    nuke.execute = execute
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    return nuke, write


def _registered_global_callback_nuke(monkeypatch, actual_path, stale_path, *, unreadable_file_knob=False):
    """Fake Nuke modeling a render callback registered process-wide through
    ``nuke.add*`` (e.g. ``nuke.addBeforeFrameRender``), NOT one of the four
    Write-node callback knobs. All four knobs stay empty, but during
    ``execute()`` the registered callback repoints the active ``file`` knob to
    a pre-existing, decodable stale PNG. A post-only (or empty before/after)
    knob snapshot cannot see this callback; the changed raw ``file`` knob is
    the only observable signal.

    ``unreadable_file_knob`` models a callback context whose read-only
    baseline cannot be established at all, which must also fail closed.

    In-process fake only; NOT native Nuke render acceptance. No host is
    launched and no user callback is executed or parsed: the repoint is a
    plain assignment and the empty knob strings are merely inspected.
    """
    values = {
        "file": actual_path,
        "frame_mode": "expression",
        "frame": "frame",
        "file_type": "png",
        "beforeRender": "",
        "beforeFrameRender": "",
        "afterFrameRender": "",
        "afterRender": "",
    }

    class Knob:
        def __init__(self, value):
            self._value = value

        def value(self):
            return self._value

    class Write:
        def Class(self):
            return "Write"

        def __getitem__(self, name):
            if name == "file" and unreadable_file_knob:
                raise RuntimeError("file knob unreadable")
            return Knob(values.get(name))

    write = Write()

    def filename(_node):
        return values["file"]  # ACTIVE file knob, read live

    def execute(_node, _first, _last, _step):
        # A globally registered render callback repoints `file` to a stale,
        # decodable output while the four Write knobs stay empty.
        values["file"] = stale_path

    nuke = ModuleType("nuke")
    nuke.toNode = lambda _name: write
    nuke.filename = filename
    nuke.execute = execute
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    return nuke, write


def _load_render_module():
    path = (
        Path(__file__).parent.parent
        / "src"
        / "dcc_mcp_nuke"
        / "skills"
        / "nuke-layered-compositing"
        / "scripts"
        / "render_write_node.py"
    )
    spec = importlib.util.spec_from_file_location("render_write_node", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_render_write_node_forwards_step(monkeypatch):
    module = _load_render_module()

    nuke, write = _fake_nuke(monkeypatch, "/renders/final.%04d.png")

    result = module.main.__wrapped__("Final", 1, 480, step=4)

    nuke.execute.assert_called_once_with(write, 1, 480, 4)
    assert result["context"]["step"] == 4


@pytest.mark.parametrize(
    ("first_frame", "last_frame", "step", "error"),
    [(2, 1, 1, "last_frame=1 is less than first_frame=2"), (1, 1, 0, "step=0 is less than 1")],
)
def test_render_write_node_returns_error_for_invalid_range_or_step(monkeypatch, first_frame, last_frame, step, error):
    module = _load_render_module()
    monkeypatch.setitem(sys.modules, "nuke", ModuleType("nuke"))

    result = module.main.__wrapped__("Final", first_frame, last_frame, step=step)

    assert result["success"] is False
    assert result["error"] == error


def test_render_write_node_proves_real_output_files_after_execute(monkeypatch, tmp_path):
    module = _load_render_module()
    directory = tmp_path / "renders"
    directory.mkdir()
    for frame in (1, 2, 3):
        (directory / f"final.{frame:04d}.png").write_bytes(_PNG)

    nuke, write = _fake_nuke(monkeypatch, str(directory / "final.%04d.png"))

    result = module.main.__wrapped__("Final", 1, 3, step=1)

    nuke.execute.assert_called_once_with(write, 1, 3, 1)
    assert result["success"] is True
    assert result["postcondition"]["verified"] is True
    verification = result["context"]["output_verification"]
    assert verification["verified"] is True
    assert verification["mode"] == "sequence"
    assert verification["checked_frame_count"] == 3
    assert verification["native_render_acceptance"] == "not_performed"


def test_successful_execute_without_output_files_is_not_verified(monkeypatch, tmp_path):
    """A returning nuke.execute must not be reported as verified output."""
    module = _load_render_module()

    nuke, write = _fake_nuke(monkeypatch, str(tmp_path / "missing.%04d.png"))

    result = module.main.__wrapped__("Final", 1, 3, step=1)

    nuke.execute.assert_called_once_with(write, 1, 3, 1)
    assert result["success"] is True
    assert result["postcondition"]["verified"] is False
    verification = result["context"]["output_verification"]
    assert verification["verified"] is False
    assert verification["missing_frames"] == [1, 2, 3]
    assert "NOT native Nuke render acceptance" in verification["scope"]


def test_unverifiable_output_path_is_explicitly_unknown(monkeypatch):
    module = _load_render_module()

    # An active filename that is not a bounded literal path cannot be checked.
    _fake_nuke(monkeypatch, None)

    result = module.main.__wrapped__("Final", 1, 1)

    assert result["success"] is True
    assert result["postcondition"]["verified"] is False
    verification = result["context"]["output_verification"]
    assert verification["mode"] == "unknown"
    assert verification["content_validation"] == "not_performed"
    assert verification["content_acceptance"] == "unknown"
    assert verification["native_render_acceptance"] == "not_performed"


# --- Active filename (proxy) and Write frame-remapping regressions ------------
# Fake Nuke host only; NOT native Nuke render acceptance.


def test_proxy_mode_uses_the_active_filename(monkeypatch, tmp_path):
    """In proxy mode nuke.filename(node) is the active path, not the file knob."""
    module = _load_render_module()
    full_size = tmp_path / "full.%04d.png"
    active = tmp_path / "proxy.%04d.png"
    (tmp_path / "full.0001.png").write_bytes(_PNG)
    (tmp_path / "proxy.0001.png").write_bytes(_PNG)

    nuke, write = _fake_nuke(monkeypatch, str(full_size), active_path=str(active))

    result = module.main.__wrapped__("Final", 1, 1)

    nuke.filename.assert_called_once_with(write)
    assert result["context"]["output_path"] == str(active)
    verification = result["context"]["output_verification"]
    assert verification["verified"] is True
    assert verification["binding"]["output_path"] == str(active)


def test_proxy_mode_does_not_verify_a_stale_full_size_file(monkeypatch, tmp_path):
    """Only the stale full-size file exists: the active proxy output is missing."""
    module = _load_render_module()
    full_size = tmp_path / "full.%04d.png"
    active = tmp_path / "proxy.%04d.png"
    (tmp_path / "full.0001.png").write_bytes(_PNG)

    _fake_nuke(monkeypatch, str(full_size), active_path=str(active))

    result = module.main.__wrapped__("Final", 1, 1)

    assert result["postcondition"]["verified"] is False
    verification = result["context"]["output_verification"]
    assert verification["binding"]["output_path"] == str(active)
    assert verification["missing_frames"] == [1]


def test_offset_mapping_verifies_the_rendered_frame_number(monkeypatch, tmp_path):
    """frame_mode=offset with frame=-499 maps timeline 500 to output frame 1."""
    module = _load_render_module()
    active = tmp_path / "shot.%04d.png"
    (tmp_path / "shot.0001.png").write_bytes(_PNG)
    (tmp_path / "shot.0500.png").write_bytes(_PNG)  # stale timeline-named file

    _fake_nuke(monkeypatch, str(active), frame_mode="offset", frame=-499)

    result = module.main.__wrapped__("Final", 500, 500)

    verification = result["context"]["output_verification"]
    assert verification["verified"] is True
    assert verification["expected_frames"] == [1]
    assert verification["output_frame_mapping"]["timeline_first_frame"] == 500
    assert verification["output_frame_mapping"]["first_output_frame"] == 1


def test_offset_mapping_never_validates_a_stale_timeline_file(monkeypatch, tmp_path):
    """The real renumbered output is absent; a stale timeline file must not pass."""
    module = _load_render_module()
    active = tmp_path / "shot.%04d.png"
    (tmp_path / "shot.0500.png").write_bytes(_PNG)  # stale timeline-named file

    _fake_nuke(monkeypatch, str(active), frame_mode="offset", frame=-499)

    result = module.main.__wrapped__("Final", 500, 500)

    assert result["postcondition"]["verified"] is False
    verification = result["context"]["output_verification"]
    assert verification["expected_frames"] == [1]
    assert verification["missing_frames"] == [1]


def test_numeric_enum_frame_mode_is_resolved_without_guessing(monkeypatch, tmp_path):
    """Real Nuke enum knobs return an index; values() yields the mode name."""
    module = _load_render_module()
    active = tmp_path / "shot.%04d.png"
    (tmp_path / "shot.0001.png").write_bytes(_PNG)

    _fake_nuke(monkeypatch, str(active), frame_mode=2, frame=-499)

    result = module.main.__wrapped__("Final", 500, 500)

    assert result["context"]["output_verification"]["expected_frames"] == [1]
    assert result["postcondition"]["verified"] is True


def test_start_at_mapping_is_unknown_without_a_proven_sequence_origin(monkeypatch, tmp_path):
    """`start at` renumbers against the first frame of the clip/sequence, not
    the first requested frame (Foundry "Changing the Numbering of Rendered
    Frames"). That origin cannot be proven from the Write node alone, so the
    mapping stays unknown instead of substituting first_frame for it."""
    module = _load_render_module()
    active = tmp_path / "shot.%04d.png"
    for frame in (1, 2):
        (tmp_path / f"shot.{frame:04d}.png").write_bytes(_PNG)

    _fake_nuke(monkeypatch, str(active), frame_mode="start at", frame=1)

    result = module.main.__wrapped__("Final", 500, 501)

    verification = result["context"]["output_verification"]
    assert result["postcondition"]["verified"] is False
    assert verification["mode"] == "unknown"
    assert verification["expected_frames"] == []


def test_start_at_partial_range_does_not_verify_a_stale_output(monkeypatch, tmp_path):
    """Fixed clip origin 500, start at=1, a partial request for timeline 505
    would write output frame 6. A pre-existing shot.0001.png must not make the
    wrapper report verified=true for the stale frame-1 file."""
    module = _load_render_module()
    active = tmp_path / "shot.%04d.png"
    (tmp_path / "shot.0001.png").write_bytes(_PNG)  # stale output from an earlier render

    _fake_nuke(monkeypatch, str(active), frame_mode="start at", frame=1)

    result = module.main.__wrapped__("Final", 505, 505)

    verification = result["context"]["output_verification"]
    assert result["postcondition"]["verified"] is False
    assert verification["mode"] == "unknown"
    assert verification["expected_frames"] == []


def test_start_at_with_non_unit_step_is_unknown(monkeypatch, tmp_path):
    """A renumbered sequence with step>1 has no provable ordering here."""
    module = _load_render_module()
    active = tmp_path / "shot.%04d.png"
    (tmp_path / "shot.0001.png").write_bytes(_PNG)

    _fake_nuke(monkeypatch, str(active), frame_mode="start at", frame=1)

    result = module.main.__wrapped__("Final", 500, 503, step=2)

    assert result["postcondition"]["verified"] is False
    assert result["context"]["output_verification"]["mode"] == "unknown"


@pytest.mark.parametrize(
    ("frame_mode", "frame"),
    [
        pytest.param("expression", "frame*2", id="arbitrary_expression"),
        pytest.param(None, "frame", id="unreadable_mode"),
    ],
)
def test_unresolvable_mapping_is_unknown_not_stale_verification(monkeypatch, tmp_path, frame_mode, frame):
    """Arbitrary host expressions are never evaluated; verification stays
    unknown instead of checking stale timeline-named files."""
    module = _load_render_module()
    active = tmp_path / "shot.%04d.png"
    (tmp_path / "shot.0500.png").write_bytes(_PNG)  # stale timeline-named file

    _fake_nuke(monkeypatch, str(active), frame_mode=frame_mode, frame=frame)

    result = module.main.__wrapped__("Final", 500, 500)

    assert result["postcondition"]["verified"] is False
    verification = result["context"]["output_verification"]
    assert verification["mode"] == "unknown"
    assert verification["content_validation"] == "not_performed"
    assert verification["content_acceptance"] == "unknown"
    assert verification["content_checks"] == {}
    assert verification["expected_frames"] == []
    assert verification["native_render_acceptance"] == "not_performed"


# --- Render callbacks: post-execute active path --------------------------------
# Fake Nuke host only; NOT native Nuke render acceptance.


def test_before_render_path_change_verifies_the_post_execute_output(monkeypatch, tmp_path):
    """beforeRender runs inside execute() and may change the active path; the
    wrapper must bind and verify the post-execute path, not the stale one."""
    module = _load_render_module()
    old = str(tmp_path / "old.%04d.png")
    new = str(tmp_path / "new.%04d.png")
    (tmp_path / "old.0001.png").write_bytes(_PNG)  # stale pre-execute file
    (tmp_path / "new.0001.png").write_bytes(_PNG)  # real post-callback output

    _callback_nuke(monkeypatch, old, new)

    result = module.main.__wrapped__("Final", 1, 1)

    verification = result["context"]["output_verification"]
    assert result["context"]["output_path"] == new
    assert verification["binding"]["output_path"] == new
    assert result["postcondition"]["verified"] is True


def test_stale_pre_execute_path_is_not_verified_when_callback_changes_output(monkeypatch, tmp_path):
    """A stale pre-execute file must not be accepted when beforeRender moved the
    active path and the new output is missing."""
    module = _load_render_module()
    old = str(tmp_path / "old.%04d.png")
    new = str(tmp_path / "new.%04d.png")
    (tmp_path / "old.0001.png").write_bytes(_PNG)  # stale
    # no new.0001.png: the post-callback output is missing

    _callback_nuke(monkeypatch, old, new)

    result = module.main.__wrapped__("Final", 1, 1)

    verification = result["context"]["output_verification"]
    assert result["context"]["output_path"] == new
    assert verification["binding"]["output_path"] == new
    assert result["postcondition"]["verified"] is False
    assert verification["missing_frames"] == [1]


def test_per_frame_callback_forces_unknown_verification(monkeypatch, tmp_path):
    """A frame-level callback can select a different path per frame, so one
    final filename cannot prove the output set; verification stays unknown."""
    module = _load_render_module()
    path = str(tmp_path / "shot.%04d.png")
    (tmp_path / "shot.0001.png").write_bytes(_PNG)

    _callback_nuke(monkeypatch, path, path, frame_callback="select a per-frame path")

    result = module.main.__wrapped__("Final", 1, 1)

    verification = result["context"]["output_verification"]
    assert verification["mode"] == "unknown"
    assert result["postcondition"]["verified"] is False


def test_unreadable_callback_state_fails_closed_to_unknown(monkeypatch, tmp_path):
    """An unreadable callback knob must not be treated as 'no callbacks';
    verification fails closed to unknown."""
    module = _load_render_module()
    path = str(tmp_path / "shot.%04d.png")
    (tmp_path / "shot.0001.png").write_bytes(_PNG)

    _callback_nuke(monkeypatch, path, path, read_error_knob="beforeFrameRender")

    result = module.main.__wrapped__("Final", 1, 1)

    assert result["context"]["output_verification"]["mode"] == "unknown"
    assert result["postcondition"]["verified"] is False


def test_self_clearing_after_render_callback_does_not_verify_stale_output(monkeypatch, tmp_path):
    """An afterRender callback set BEFORE execute but cleared DURING the render
    cannot be ruled out by a post-only snapshot. The pre-execute state must be
    combined with the post state, so verification stays unknown instead of
    falling back to a stale pre-existing file. In-process fake; NOT native Nuke
    render acceptance, no host launched, no user callback executed or parsed.
    """
    module = _load_render_module()
    actual = str(tmp_path / "actual.%04d.png")
    stale = str(tmp_path / "stale.%04d.png")
    (tmp_path / "stale.0001.png").write_bytes(_PNG)  # stale pre-existing decodable output

    _self_clearing_callback_nuke(monkeypatch, actual, stale)

    result = module.main.__wrapped__("Final", 1, 1)

    verification = result["context"]["output_verification"]
    assert result["postcondition"]["verified"] is False
    assert verification["mode"] == "unknown"
    assert verification["content_validation"] == "not_performed"
    assert verification["content_acceptance"] == "unknown"
    assert verification["content_checks"] == {}
    assert verification["expected_frames"] == []
    assert verification["native_render_acceptance"] == "not_performed"
    # The stale decodable file exists; it must never be accepted as proof. The
    # unknown verdict must come from the callback, not an unrelated gate.
    assert "callback" in verification["reason"]


@pytest.mark.parametrize(
    "unreadable_file_knob",
    [
        pytest.param(False, id="global_callback_repoints_to_stale"),
        pytest.param(True, id="callback_context_unreadable"),
    ],
)
def test_registered_global_callback_forces_unknown_verification(monkeypatch, tmp_path, unreadable_file_knob):
    """Four empty Write knobs do NOT prove no global render callback exists.

    A callback registered with ``nuke.add*`` (or a temporary handler a
    ``beforeRender`` script registers and removes during execute) can repoint
    ``file`` to a pre-existing decodable PNG. Nuke has no public API to
    enumerate registered handlers, so the wrapper must fail closed to
    ``unknown`` instead of verifying the single final filename. The second
    case covers an unreadable callback context (``file`` knob baseline cannot
    be read): verification also stays ``unknown``, not a render failure.

    In-process fake; NOT native Nuke acceptance, no host launched, no user
    callback executed or parsed.
    """
    module = _load_render_module()
    actual = str(tmp_path / "actual.%04d.png")
    stale = str(tmp_path / "stale.%04d.png")
    (tmp_path / "stale.0001.png").write_bytes(_PNG)  # stale pre-existing decodable output

    _registered_global_callback_nuke(monkeypatch, actual, stale, unreadable_file_knob=unreadable_file_knob)

    result = module.main.__wrapped__("Final", 1, 1)

    verification = result["context"]["output_verification"]
    assert result["success"] is True  # a render, not an invented failure
    assert result["postcondition"]["verified"] is False
    assert verification["mode"] == "unknown"
    assert verification["content_validation"] == "not_performed"
    assert verification["content_acceptance"] == "unknown"
    assert verification["content_checks"] == {}
    assert verification["expected_frames"] == []
    assert verification["native_render_acceptance"] == "not_performed"
    assert "callback" in verification["reason"]
    # The stale decodable file exists at the repointed active path; it must
    # never be accepted as proof of this render.
    assert (tmp_path / "stale.0001.png").read_bytes() == _PNG
    if not unreadable_file_knob:
        assert result["context"]["output_path"] == stale


# --- Output format binding -----------------------------------------------------
# Fake Nuke host only; NOT native Nuke render acceptance.


def test_exr_path_with_png_bytes_is_not_verified(monkeypatch, tmp_path):
    """A PNG at an .exr output path must not be claimed verified: the wrapper
    binds expected_format to the effective output format (exr)."""
    module = _load_render_module()
    active = tmp_path / "shot.%04d.exr"
    (tmp_path / "shot.0001.exr").write_bytes(_PNG)  # wrong-format file

    _fake_nuke(monkeypatch, str(active))

    result = module.main.__wrapped__("Final", 1, 1)

    verification = result["context"]["output_verification"]
    assert result["postcondition"]["verified"] is False
    assert verification["binding"]["expected_format"] == "exr"
    assert verification["unreadable_frames"] == [1]


def test_conflicting_file_type_and_path_stays_unknown(monkeypatch, tmp_path):
    """file_type and the path extension disagree; the format is not provable,
    so verification stays unknown instead of falling back to magic sniffing."""
    module = _load_render_module()
    active = tmp_path / "shot.%04d.exr"
    (tmp_path / "shot.0001.exr").write_bytes(_PNG)

    _fake_nuke(monkeypatch, str(active), file_type="png")

    result = module.main.__wrapped__("Final", 1, 1)

    assert result["context"]["output_verification"]["mode"] == "unknown"
    assert result["postcondition"]["verified"] is False


def test_unknown_output_format_stays_unknown(monkeypatch, tmp_path):
    """No recognizable format (empty file_type, no path extension): unknown,
    never unrestricted magic-number sniffing."""
    module = _load_render_module()
    active = tmp_path / "shot"
    (tmp_path / "shot").write_bytes(_PNG)

    _fake_nuke(monkeypatch, str(active))

    result = module.main.__wrapped__("Final", 1, 1)

    assert result["context"]["output_verification"]["mode"] == "unknown"
    assert result["postcondition"]["verified"] is False


def test_format_alias_is_normalized_for_binding(monkeypatch, tmp_path):
    """The jpeg alias is normalized to jpg when binding expected_format."""
    module = _load_render_module()
    active = tmp_path / "shot.%04d.jpeg"
    (tmp_path / "shot.0001.jpeg").write_bytes(_JPEG)

    _fake_nuke(monkeypatch, str(active), file_type="jpeg")

    result = module.main.__wrapped__("Final", 1, 1)

    verification = result["context"]["output_verification"]
    assert verification["binding"]["expected_format"] == "jpg"
    assert result["postcondition"]["verified"] is False  # jpg is signature-only


def test_unreadable_file_type_stays_unknown_not_path_inferred(monkeypatch, tmp_path):
    """An UNREADABLE real file_type is not a readable empty one: inferring the
    format from the path suffix would not be a proven binding (the real value
    could override it). Verification must stay unknown even though a decodable
    PNG sits at the .png path. In-process fake; NOT native Nuke acceptance.

    This is the correct expectation for the counterexample: the bug is a
    post-read failure collapsing to empty and path-inferring "png", then
    reporting verified=true on the file; it must NOT be preserved as a contract.
    """
    module = _load_render_module()
    active = tmp_path / "shot.%04d.png"
    (tmp_path / "shot.0001.png").write_bytes(_PNG)  # a decodable file IS present

    _fake_nuke(monkeypatch, str(active), unreadable_knob="file_type")

    result = module.main.__wrapped__("Final", 1, 1)

    verification = result["context"]["output_verification"]
    assert result["postcondition"]["verified"] is False
    assert verification["mode"] == "unknown"
    assert verification["content_validation"] == "not_performed"
    assert verification["content_acceptance"] == "unknown"
    assert verification["content_checks"] == {}
    assert verification["expected_frames"] == []
    assert verification["native_render_acceptance"] == "not_performed"
    # The unknown verdict must stem from the unreadable file_type, not a path
    # or callback gate; no format binding / sniffing took place.
    assert "file_type" in verification["reason"]


def test_explicitly_empty_file_type_allows_path_extension_inference(monkeypatch, tmp_path):
    """A readable, explicitly empty file_type is NOT unreadable: path-extension
    inference remains a proven, legal binding (real Write contract). Guards the
    distinction so the unreadable-file_type fix cannot over-correct this case.
    """
    module = _load_render_module()
    active = tmp_path / "shot.%04d.exr"
    (tmp_path / "shot.0001.exr").write_bytes(_PNG)  # wrong-format bytes

    _fake_nuke(monkeypatch, str(active), file_type="")

    result = module.main.__wrapped__("Final", 1, 1)

    verification = result["context"]["output_verification"]
    # A "binding" key proves verification ran with a path-inferred format
    # (mode would be "unknown" with no binding if empty collapsed to unreadable).
    assert verification["binding"]["expected_format"] == "exr"
    assert verification["unreadable_frames"] == [1]
    assert result["postcondition"]["verified"] is False  # PNG bytes at .exr
