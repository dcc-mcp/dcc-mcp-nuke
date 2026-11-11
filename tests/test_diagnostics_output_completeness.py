"""Group-scoped content metadata must describe the WHOLE requested frame set.

A real Nuke host readback found that missing outputs could still be reported
with ``content_validation="full_decode"`` and ``content_acceptance="verified"``
even though ``verified`` (top level) was false and ``content_checks`` was empty
or incomplete. These are the specification tests for that contradiction.

They use synthetic temp files and in-repo fakes only: no Nuke host, no service,
no credentials, no network. They are filesystem readback checks, NOT native
Nuke render acceptance. A "full decode" claim requires a decodable content
check for every required frame; a legal-but-unsupported image stays unknown.
"""

from __future__ import annotations

import base64
import importlib.util
import struct
import sys
import zlib
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

from dcc_mcp_nuke.diagnostics import verify_render_output

# A genuinely decodable 2x2 RGB PNG (IHDR + IDAT + IEND with valid CRCs).
_VALID_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR42mO4Iycnd0eOAUIBACAqBGGFHUPdAAAAAElFTkSuQmCC"
)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SUCCESS_REASON = "all expected frames present, nonempty and readable"


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _ihdr(width=2, height=2, bit_depth=8, color_type=2, compression=0, filter_method=0, interlace=0):
    return struct.pack(">IIBBBBB", width, height, bit_depth, color_type, compression, filter_method, interlace)


def _raw_scanlines(width=2, height=2, bit_depth=8, color_type=2):
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    row_bytes = (width * channels * bit_depth + 7) // 8
    return b"\x00" * (height * (row_bytes + 1))


def _legal_unknown_indexed_png() -> bytes:
    """A legal indexed PNG (PLTE present) that this decoder cannot validate."""
    ihdr = _ihdr(color_type=3)
    raw = _raw_scanlines(color_type=3)
    return (
        _PNG_SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"PLTE", b"\x00\x00\x00" * 4)
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )


def _write_png(path: Path, data: bytes = _VALID_PNG) -> Path:
    path.write_bytes(data)
    return path


def _assert_group_metadata_consistent(result: dict) -> None:
    """Content fields are group-scoped: they must agree with frame accounting.

    A ``full_decode``/``verified`` claim is only honest when every checked frame
    has a ``decodable`` content check and no frame is missing, empty, unreadable
    or unverified. ``content_checks`` has an entry for exactly the present,
    non-zero frames (missing and zero-byte frames add none).
    """
    claims_full_decode = result["content_validation"] == "full_decode"
    claims_verified = result["content_acceptance"] == "verified"
    assert claims_full_decode == claims_verified, "full_decode and verified must agree"

    if claims_full_decode:
        assert result["verified"] is True
        assert result["checked_frame_count"] > 0
        assert result["missing_frame_count"] == 0
        assert result["zero_byte_frames"] == []
        assert result["unreadable_frames"] == []
        assert result["content_unverified_frames"] == []
        assert len(result["content_checks"]) == result["checked_frame_count"]
        assert all(value == "decodable" for value in result["content_checks"].values())

    assert len(result["content_checks"]) == result["present_frame_count"] - len(result["zero_byte_frames"])


# --- The contradiction: missing/empty outputs must not claim a full decode ----


def test_all_required_outputs_missing_single_is_not_full_decode(tmp_path):
    target = tmp_path / "no_such.%04d.png"
    result = verify_render_output(output_path=str(target), first_frame=1, last_frame=2, expected_format="png")
    assert result["verified"] is False
    assert result["missing_frame_count"] == 2
    assert result["content_checks"] == {}
    assert result["content_validation"] != "full_decode"
    assert result["content_acceptance"] != "verified"
    assert result["content_acceptance"] == "unknown"  # nothing was decoded
    assert "missing" in result["reason"]
    assert result["reason"] != _SUCCESS_REASON
    _assert_group_metadata_consistent(result)


def test_all_required_outputs_missing_sequence_is_not_full_decode(tmp_path):
    target = tmp_path / "absent.%04d.png"
    result = verify_render_output(output_path=str(target), first_frame=10, last_frame=12, expected_format="png")
    assert result["verified"] is False
    assert result["missing_frame_count"] == 3
    assert result["content_checks"] == {}
    assert result["content_validation"] != "full_decode"
    assert result["content_acceptance"] != "verified"
    _assert_group_metadata_consistent(result)


def test_partial_sequence_missing_is_not_full_decode(tmp_path):
    for frame in (1, 2, 3):
        _write_png(tmp_path / f"frame.{frame:04d}.png")
    target = tmp_path / "frame.%04d.png"  # frame 4 is missing
    result = verify_render_output(output_path=str(target), first_frame=1, last_frame=4, expected_format="png")
    assert result["verified"] is False
    assert result["missing_frame_count"] == 1
    assert len(result["content_checks"]) == 3
    assert result["content_validation"] != "full_decode"
    assert result["content_acceptance"] != "verified"
    assert "missing" in result["reason"]
    _assert_group_metadata_consistent(result)


def test_zero_byte_output_is_not_full_decode(tmp_path):
    (tmp_path / "empty.png").write_bytes(b"")
    target = tmp_path / "empty.png"
    result = verify_render_output(output_path=str(target), first_frame=1, last_frame=1, expected_format="png")
    assert result["verified"] is False
    assert result["zero_byte_frames"] == [1]
    assert result["present_frame_count"] == 1
    assert result["content_checks"] == {}
    assert result["content_validation"] != "full_decode"
    assert result["content_acceptance"] != "verified"
    assert "zero-byte" in result["reason"]
    _assert_group_metadata_consistent(result)


# --- Regression guards: valid, invalid and legal-unknown paths stay correct ---


def test_valid_single_frame_stays_full_decode(tmp_path):
    target = _write_png(tmp_path / "single.png")
    result = verify_render_output(output_path=str(target), first_frame=1, last_frame=1, expected_format="png")
    assert result["verified"] is True
    assert result["content_validation"] == "full_decode"
    assert result["content_acceptance"] == "verified"
    assert result["content_checks"] == {"1": "decodable"}
    _assert_group_metadata_consistent(result)


def test_valid_sequence_stays_full_decode(tmp_path):
    for frame in (1, 2, 3):
        _write_png(tmp_path / f"seq.{frame:04d}.png")
    target = tmp_path / "seq.%04d.png"
    result = verify_render_output(output_path=str(target), first_frame=1, last_frame=3, expected_format="png")
    assert result["verified"] is True
    assert result["content_validation"] == "full_decode"
    assert result["content_acceptance"] == "verified"
    assert len(result["content_checks"]) == 3
    _assert_group_metadata_consistent(result)


def test_present_but_invalid_output_is_failed(tmp_path):
    target = _write_png(tmp_path / "truncated.png", _PNG_SIGNATURE)
    result = verify_render_output(output_path=str(target), first_frame=1, last_frame=1, expected_format="png")
    assert result["verified"] is False
    assert result["content_validation"] == "decode_failed"
    assert result["content_acceptance"] == "failed"
    assert result["content_acceptance"] != "verified"
    assert result["unreadable_frames"] == [1]
    assert result["reason"] != _SUCCESS_REASON
    _assert_group_metadata_consistent(result)


def test_partial_missing_with_invalid_frame_is_failed(tmp_path):
    _write_png(tmp_path / "mix.0001.png")
    _write_png(tmp_path / "mix.0002.png", _PNG_SIGNATURE)  # present but invalid
    target = tmp_path / "mix.%04d.png"  # frame 3 missing
    result = verify_render_output(output_path=str(target), first_frame=1, last_frame=3, expected_format="png")
    assert result["verified"] is False
    assert result["missing_frame_count"] == 1
    assert result["unreadable_frames"] == [2]
    assert result["content_validation"] == "decode_failed"
    assert result["content_acceptance"] == "failed"
    assert result["content_acceptance"] != "verified"
    _assert_group_metadata_consistent(result)


def test_legal_unknown_png_stays_unknown(tmp_path):
    target = _write_png(tmp_path / "indexed.png", _legal_unknown_indexed_png())
    result = verify_render_output(output_path=str(target), first_frame=1, last_frame=1, expected_format="png")
    assert result["verified"] is False
    assert result["content_validation"] == "signature_only"
    assert result["content_acceptance"] == "unknown"
    assert result["content_unverified_frames"] == [1]
    assert result["content_checks"]["1"].startswith("unknown:")
    _assert_group_metadata_consistent(result)


# --- Typed tool wrapper must propagate the corrected group metadata ----------


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
    spec = importlib.util.spec_from_file_location("render_write_node_completeness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_nuke(monkeypatch, output_path: str):
    write = MagicMock()
    write.Class.return_value = "Write"
    write.__getitem__.return_value.value.return_value = output_path
    nuke = ModuleType("nuke")
    nuke.toNode = MagicMock(return_value=write)
    nuke.execute = MagicMock()
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    return nuke, write


def test_typed_wrapper_missing_outputs_metadata_consistent(monkeypatch, tmp_path):
    module = _load_render_module()
    _fake_nuke(monkeypatch, str(tmp_path / "missing.%04d.png"))
    result = module.main.__wrapped__("Final", 1, 3, step=1)
    assert result["success"] is True
    assert result["postcondition"]["verified"] is False
    verification = result["context"]["output_verification"]
    assert verification["verified"] is False
    assert verification["missing_frame_count"] == 3
    assert verification["content_checks"] == {}
    assert verification["content_validation"] != "full_decode"
    assert verification["content_acceptance"] != "verified"
    assert "missing" in verification["reason"]
    _assert_group_metadata_consistent(verification)


def test_typed_wrapper_valid_sequence_metadata(monkeypatch, tmp_path):
    module = _load_render_module()
    for frame in (1, 2, 3):
        _write_png(tmp_path / f"good.{frame:04d}.png")
    nuke, write = _fake_nuke(monkeypatch, str(tmp_path / "good.%04d.png"))
    result = module.main.__wrapped__("Final", 1, 3, step=1)
    nuke.execute.assert_called_once_with(write, 1, 3, 1)
    assert result["success"] is True
    assert result["postcondition"]["verified"] is True
    verification = result["context"]["output_verification"]
    assert verification["verified"] is True
    assert verification["content_validation"] == "full_decode"
    assert verification["content_acceptance"] == "verified"
    assert len(verification["content_checks"]) == 3
    _assert_group_metadata_consistent(verification)
