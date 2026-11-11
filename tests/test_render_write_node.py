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


def _fake_nuke(monkeypatch, file_path, *, active_path=None, frame_mode="expression", frame="frame"):
    """Install a fake Nuke host whose active filename may differ from `file`."""
    values = {"file": file_path, "frame_mode": frame_mode, "frame": frame}
    write = MagicMock()
    write.Class.return_value = "Write"
    write.__getitem__.side_effect = lambda name: _Knob(values.get(name))
    nuke = ModuleType("nuke")
    nuke.toNode = MagicMock(return_value=write)
    nuke.execute = MagicMock()
    nuke.filename = MagicMock(return_value=file_path if active_path is None else active_path)
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


def test_start_at_mapping_uses_the_renumbered_frame(monkeypatch, tmp_path):
    """start at=1 over timeline 500..501 renders output frames 1..2."""
    module = _load_render_module()
    active = tmp_path / "shot.%04d.png"
    for frame in (1, 2):
        (tmp_path / f"shot.{frame:04d}.png").write_bytes(_PNG)

    _fake_nuke(monkeypatch, str(active), frame_mode="start at", frame=1)

    result = module.main.__wrapped__("Final", 500, 501)

    verification = result["context"]["output_verification"]
    assert verification["verified"] is True
    assert verification["expected_frames"] == [1, 2]


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
