"""Isolated counter-examples for the post-install diagnostics contracts.

These tests use synthetic temp files/packages and in-repo fakes only. They never
import the Nuke host, start a service, read credentials or make network calls.
They are NOT native Nuke render acceptance.
"""

from __future__ import annotations

import base64
import builtins
import importlib.machinery
import struct
import sys
import zlib

import pytest

from dcc_mcp_nuke.diagnostics import diagnose_dependencies, verify_render_output

# A genuinely decodable 2x2 RGB PNG (IHDR + IDAT + IEND with valid CRCs).
_VALID_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR42mO4Iycnd0eOAUIBACAqBGGFHUPdAAAAAElFTkSuQmCC"
)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _png_with_idat(payload: bytes) -> bytes:
    """Rebuild a structurally valid PNG whose IDAT payload is arbitrary."""
    ihdr = _VALID_PNG[8:33]
    return _PNG_SIGNATURE + ihdr + _chunk(b"IDAT", payload) + _chunk(b"IEND", b"")


# --- Counter-example 1: an 8-byte PNG magic is not a readable image ------------


def test_real_decodable_png_is_content_verified(tmp_path):
    target = tmp_path / "final.png"
    target.write_bytes(_VALID_PNG)
    result = verify_render_output(output_path=str(target), first_frame=1, last_frame=1, expected_format="png")
    assert result["verified"] is True
    assert result["content_validation"] == "full_decode"
    assert result["content_acceptance"] == "verified"
    assert result["content_checks"] == {"1": "decodable"}


def test_eight_byte_png_magic_is_not_readable_or_verified(tmp_path):
    target = tmp_path / "truncated.png"
    target.write_bytes(_PNG_SIGNATURE)  # exactly the 8-byte magic, nothing else
    result = verify_render_output(output_path=str(target), first_frame=1, last_frame=1, expected_format="png")
    assert result["verified"] is False, "signature-only file must not be reported as verified"
    assert result["content_acceptance"] == "failed"
    assert result["reason"] != "all expected frames present, nonempty and readable"
    assert result["unreadable_frames"] == [1]
    assert result["content_checks"]["1"].startswith("invalid:")


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(_VALID_PNG[:33], id="truncated_after_ihdr"),
        pytest.param(_VALID_PNG[:-4], id="truncated_before_iend"),
        pytest.param(_VALID_PNG[:29] + bytes([_VALID_PNG[29] ^ 1]) + _VALID_PNG[30:], id="bad_crc"),
        pytest.param(_png_with_idat(b"not zlib data"), id="idat_not_decodable"),
    ],
)
def test_broken_png_structure_is_not_readable_or_verified(tmp_path, payload):
    target = tmp_path / "broken.png"
    target.write_bytes(payload)
    result = verify_render_output(output_path=str(target), first_frame=1, last_frame=1, expected_format="png")
    assert result["verified"] is False
    assert result["content_acceptance"] == "failed"
    assert result["unreadable_frames"] == [1]
    assert result["reason"] != "all expected frames present, nonempty and readable"


def test_signature_only_format_is_explicitly_unknown_not_verified(tmp_path):
    """JPEG/EXR have no decoder here: signature-only is UNKNOWN, never readable."""
    target = tmp_path / "frame.exr"
    target.write_bytes(b"\x76\x2f\x31\x01" + b"\x00" * 64)
    result = verify_render_output(output_path=str(target), first_frame=1, last_frame=1)
    assert result["verified"] is False
    assert result["content_validation"] == "signature_only"
    assert result["content_acceptance"] == "unknown"
    assert result["unreadable_frames"] == []
    assert result["content_unverified_frames"] == [1]
    assert result["content_checks"] == {"1": "signature_only"}
    assert "decode-validated" in result["reason"]


def test_decoder_capability_limit_is_unknown_not_a_broken_file(tmp_path):
    """An interlaced PNG is readable, but this decoder cannot validate it."""
    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 1)  # interlace = 1 (Adam7)
    payload = (
        _PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(b"\x00" * 14)) + _chunk(b"IEND", b"")
    )
    target = tmp_path / "interlaced.png"
    target.write_bytes(payload)
    result = verify_render_output(output_path=str(target), first_frame=1, last_frame=1, expected_format="png")
    assert result["verified"] is False
    assert result["content_acceptance"] == "unknown"
    assert result["unreadable_frames"] == []
    assert result["content_unverified_frames"] == [1]
    assert result["content_checks"] == {"1": "unknown:interlaced_not_validated"}


# --- Counter-example 2: locating a dotted module must not execute its parent --


def test_dotted_module_probe_does_not_execute_parent_package(tmp_path, monkeypatch):
    package = tmp_path / "synthetic_parent_probe"
    package.mkdir()
    marker = tmp_path / "parent_was_executed.marker"
    (package / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n", encoding="utf-8"
    )
    (package / "child.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "synthetic_parent_probe", raising=False)
    monkeypatch.delitem(sys.modules, "synthetic_parent_probe.child", raising=False)

    result = diagnose_dependencies(None, required_modules=["synthetic_parent_probe.child"])

    assert not marker.exists(), "diagnosis executed the parent package __init__.py"
    entry = result["modules"][0]
    assert entry["status"] == "unknown"
    assert entry["certainty"] == "unknown"
    assert entry["import_tested"] is False
    assert "synthetic_parent_probe" not in sys.modules


# --- Counter-example 3: discoverable is not import-tested ---------------------


def test_locatable_module_with_broken_transitive_dependency_is_not_importable(tmp_path, monkeypatch):
    package = tmp_path / "synthetic_broken_dep"
    package.mkdir()
    (package / "__init__.py").write_text(
        "from definitely_missing_transitive_pkg import value  # noqa: F401\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "synthetic_broken_dep", raising=False)

    # A spec is locatable without importing anything, but importing it would
    # fail on the missing transitive dependency.
    assert importlib.machinery.PathFinder.find_spec("synthetic_broken_dep", [str(tmp_path)]) is not None

    result = diagnose_dependencies(None, required_modules=["synthetic_broken_dep"])

    entry = result["modules"][0]
    assert entry["status"] == "discoverable"
    assert entry["certainty"] == "verified_discoverable_not_import_tested"
    assert entry["import_tested"] is False
    assert "not imported" in entry["certainty_note"]
    assert result["module_probe"]["import_tested"] is False
    assert result["module_probe"]["module_code_executed"] is False
    assert "synthetic_broken_dep" not in sys.modules


# --- Counter-example 4: bounded range check before any allocation -------------


_REAL_LIST = builtins.list


def _guarded_list(*args, **kwargs):
    """Block materializing a huge range so this test cannot OOM the runner."""
    if args and isinstance(args[0], range):
        try:
            size = len(args[0])
        except OverflowError:
            size = None
        if size is None or size > 1000:
            raise RuntimeError("unbounded frame range materialization attempted")
    return _REAL_LIST(*args, **kwargs)


def _verify_huge_range(monkeypatch, tmp_path, last_frame):
    with monkeypatch.context() as ctx:
        ctx.setattr(builtins, "list", _guarded_list)
        try:
            return verify_render_output(
                output_path=str(tmp_path / "shot.%04d.png"),
                first_frame=1,
                last_frame=last_frame,
                max_frames=1,
            )
        except RuntimeError as exc:
            return {"error": str(exc)}


def test_huge_frame_range_is_counted_before_allocation(monkeypatch, tmp_path):
    result = _verify_huge_range(monkeypatch, tmp_path, 1_000_000_000_001)
    assert "error" not in result, "frame range was materialized before the max_frames check"
    assert result["requested_frame_count"] == 1_000_000_000_001
    assert result["frame_limit_exceeded"] is True
    assert result["verified"] is False
    assert result["checked_frame_count"] <= 1


def test_frame_count_beyond_ssize_t_is_arithmetic_not_len_range(monkeypatch, tmp_path):
    huge = 10**30
    result = _verify_huge_range(monkeypatch, tmp_path, huge)
    assert "error" not in result, "frame range was materialized before the max_frames check"
    assert result["requested_frame_count"] == huge
    assert result["checked_frame_count"] <= 1
    assert result["frame_limit_exceeded"] is True


def test_frame_limit_boundaries_keep_the_failure_semantics(tmp_path):
    for frame in (1, 2):
        (tmp_path / f"shot.{frame:04d}.png").write_bytes(_VALID_PNG)
    base = dict(output_path=str(tmp_path / "shot.%04d.png"), expected_format="png")

    exact = verify_render_output(**base, first_frame=1, last_frame=1, max_frames=1)
    assert exact["frame_limit_exceeded"] is False
    assert exact["verified"] is True
    assert exact["requested_frame_count"] == 1

    clamped = verify_render_output(**base, first_frame=1, last_frame=3, max_frames=2)
    assert clamped["frame_limit_exceeded"] is True
    assert clamped["verified"] is False
    assert clamped["expected_frames"] == [1, 2]
    assert clamped["checked_frame_count"] == 2
