"""Counter-examples for install diagnostics, render output proof and billing transparency.

These tests use isolated temp files and in-repo fakes only. They exercise the
diagnostic contracts; they are NOT native Nuke render acceptance.
"""

from __future__ import annotations

import base64
import json
import sys
from types import ModuleType

import pytest

from dcc_mcp_nuke import diagnostics
from dcc_mcp_nuke.diagnostics import (
    describe_execution_sources,
    diagnose_dependencies,
    verify_render_output,
)

# A genuinely decodable 2x2 RGB PNG (IHDR + IDAT + IEND, valid CRCs).
_VALID_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR42mO4Iycnd0eOAUIBACAqBGGFHUPdAAAAAElFTkSuQmCC"
)
# Signature-only fixtures: no decoder is available for these formats here.
_HEADERS = {
    "jpg": b"\xff\xd8\xff\xe0\x00\x10JFIF",
    "exr": b"\x76\x2f\x31\x01" + b"\x00" * 24,
}


def _image(kind="png"):
    return _VALID_PNG if kind == "png" else _HEADERS[kind] + b"\x00" * 32


class FakeNuke:
    """Read-only plug-in inventory fake; any activating host API fails the test."""

    ALL = 1
    REGISTERED = 2
    NUKE_VERSION_STRING = "16.1v2"

    def __init__(self, *, available=(), registered=()):
        self.available = list(available)
        self.registered = list(registered)

    def pluginPath(self):
        return []

    def plugins(self, flags=0):
        return {0: [], self.ALL: self.available, self.REGISTERED: self.registered}[flags]

    def pluginAddPath(self, _path):
        pytest.fail("diagnostics must not activate plugin paths")

    def load(self, _path):
        pytest.fail("diagnostics must not load plug-ins")

    def createNode(self, _name, **_kwargs):
        pytest.fail("diagnostics must not create nodes")


# --- Area 1: dependency / missing-node diagnosis -------------------------------


def test_embedded_interpreter_is_reported_as_verified(monkeypatch):
    host = FakeNuke()
    monkeypatch.setitem(sys.modules, "nuke", host)
    result = diagnose_dependencies(host)
    assert result["interpreter"]["context"] == "nuke_embedded"
    assert result["interpreter"]["certainty"] == "verified"
    assert result["interpreter"]["facts"]["nuke_version_string"] == "16.1v2"
    name = result["interpreter"]["facts"]["sys_executable_name"]
    assert name and "/" not in name and "\\" not in name


def test_external_cpython_gets_an_actionable_repair_hint(monkeypatch):
    monkeypatch.delitem(sys.modules, "nuke", raising=False)
    monkeypatch.setattr(sys, "executable", "C:/Python312/python.exe")
    result = diagnose_dependencies(None)
    assert result["interpreter"]["context"] == "external_cpython"
    assert result["interpreter"]["certainty"] == "verified"
    assert result["interpreter"]["facts"]["nuke_module_loaded_in_this_process"] is False
    assert "--python" in result["interpreter"]["repair"]


def test_indeterminate_interpreter_is_explicitly_unknown(monkeypatch):
    other = ModuleType("nuke")
    monkeypatch.setitem(sys.modules, "nuke", other)
    result = diagnose_dependencies(FakeNuke())
    assert result["interpreter"]["context"] == "unknown"
    assert result["interpreter"]["certainty"] == "unknown"


def test_missing_module_is_reported_without_auto_install():
    result = diagnose_dependencies(None, required_modules=["vendor.nodes"], module_available=lambda _name: False)
    entry = result["modules"][0]
    assert entry["status"] == "not_found"
    assert entry["certainty"] == "verified_unavailable"
    assert entry["method"] == "caller_supplied_lookup"
    assert "install.md" in entry["repair"]
    assert result["issues"][0]["stage"] == "python_module"
    assert result["issues"][0]["code"] == "missing_module"
    assert result["safety"]["auto_install_performed"] is False


def test_importable_module_produces_no_issue():
    result = diagnose_dependencies(None, required_modules=["os"], module_available=lambda _name: True)
    assert result["modules"][0]["status"] == "importable"
    assert result["issues"] == []
    assert result["issue_counts"] == {"verified": 0, "verified_unavailable": 0, "unknown": 0}


def test_unverifiable_module_lookup_is_unknown_not_missing():
    def broken(_name):
        raise RuntimeError("lookup unavailable")

    result = diagnose_dependencies(None, required_modules=["vendor.nodes"], module_available=broken)
    assert result["modules"][0]["status"] == "unknown"
    assert result["modules"][0]["certainty"] == "unknown"
    assert result["issues"][0]["code"] == "module_check_unknown"


def test_node_class_statuses_keep_explicit_certainty_and_no_runtime_validation(tmp_path):
    gizmo = tmp_path / "Erode_Fine.gizmo"
    gizmo.write_bytes(b"metadata-only asset")
    native = tmp_path / "Unloaded.dll"
    native.write_bytes(b"metadata-only asset")
    ofx = tmp_path / "vendor.ofx"
    ofx.write_bytes(b"metadata-only asset")
    nuke = FakeNuke(available=[str(gizmo), str(native), str(ofx)], registered=["Erode_Fine"])
    result = diagnose_dependencies(
        nuke,
        required_node_classes=["Erode_Fine", "Unloaded", "Missing", "OFXcom.vendor.Bundle.Class"],
        module_available=lambda _name: True,
    )
    states = {item["subject"]: item for item in result["node_classes"]}
    assert states["Erode_Fine"]["certainty"] == "verified"
    assert states["Unloaded"]["certainty"] == "unknown"
    assert states["Missing"]["certainty"] == "verified_unavailable"
    assert states["OFXcom.vendor.Bundle.Class"]["certainty"] == "verified_unavailable"
    assert all(item["facts"]["runtime_validation"] == "not_run" for item in result["node_classes"])
    assert {"native", "ofx", "gizmo"} <= set(result["plugin_kinds_observed"])
    assert result["plugin_inventory_available"] is True
    assert "nuke_input_health__check_input_health" in json.dumps(result["existing_capabilities"])


def test_unavailable_plugin_inventory_is_unknown_not_confirmed_missing(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("host inventory unavailable")

    monkeypatch.setattr(diagnostics, "inspect_plugin_environment", fail)
    result = diagnose_dependencies(None, required_node_classes=["Missing"])
    assert result["plugin_inventory_available"] is False
    assert result["issues"][0]["code"] == "plugin_inventory_unavailable"
    assert result["issues"][0]["certainty"] == "unknown"
    assert "not confirmed missing" in result["issues"][0]["repair"]


def test_version_insufficiency_and_comfyui_are_never_inferred():
    result = diagnose_dependencies(None, module_available=lambda _name: True)
    assert result["version"]["minimum_required_contract"] == "unknown"
    assert result["version"]["insufficiency_verdict"] == "unknown"
    assert result["comfyui"]["applicable"] is False
    assert result["comfyui"]["verdict"] == "not_applicable"
    assert "no causal claim" in result["comfyui"]["reason"]
    assert "no such integration is created" in result["comfyui"]["reason"]


def test_diagnosis_rejects_unbounded_or_expression_like_identifiers():
    with pytest.raises(ValueError, match="bounded list"):
        diagnose_dependencies(None, required_modules=["os; import shutil"])
    with pytest.raises(ValueError, match="max_items"):
        diagnose_dependencies(None, max_items=0)


# --- Area 2: single-frame / sequence I/O round trip ----------------------------


def test_single_frame_with_spaces_and_unicode_is_verified_by_real_readback(tmp_path):
    directory = tmp_path / "renders 场景"
    directory.mkdir()
    target = directory / "final 镜头.png"
    target.write_bytes(_image())
    result = verify_render_output(
        output_path=str(target),
        first_frame=7,
        last_frame=7,
        expected_format="png",
        input_path="plate.%04d.exr",
    )
    assert result["verified"] is True
    assert result["mode"] == "single_frame"
    assert result["present_frames"] == [7]
    assert result["detected_formats"] == {"7": "png"}
    assert result["format_check"] == "requested_magic_number_verified"
    assert result["binding"]["input_path"] == "plate.%04d.exr"
    assert result["native_render_acceptance"] == "not_performed"


def test_sequence_round_trip_verifies_the_exact_requested_frame_set(tmp_path):
    directory = tmp_path / "renders"
    directory.mkdir()
    for frame in (1, 2, 3):
        (directory / f"shot.{frame:04d}.png").write_bytes(_image())
    result = verify_render_output(
        output_path=str(directory / "shot.%04d.png"),
        first_frame=1,
        last_frame=3,
        step=1,
        expected_format="png",
    )
    assert result["verified"] is True
    assert result["mode"] == "sequence"
    assert result["expected_frames"] == [1, 2, 3]
    assert result["checked_frame_count"] == 3
    assert result["present_frame_count"] == 3
    assert result["missing_frames"] == []
    assert result["reason"] == "all expected frames present, nonempty and readable"


def test_request_is_bound_to_frame_range_not_to_whatever_files_exist(tmp_path):
    directory = tmp_path / "renders"
    directory.mkdir()
    for frame in (1, 2):
        (directory / f"shot.{frame:04d}.png").write_bytes(_image())
    base = dict(output_path=str(directory / "shot.%04d.png"), expected_format="png")
    narrow = verify_render_output(**base, first_frame=1, last_frame=2)
    wide = verify_render_output(**base, first_frame=1, last_frame=3)
    assert narrow["verified"] is True
    assert wide["verified"] is False
    assert wide["missing_frames"] == [3]
    assert "missing output files" in wide["reason"]


def test_partial_files_after_abort_are_not_verified(tmp_path):
    directory = tmp_path / "renders"
    directory.mkdir()
    (directory / "shot.0001.png").write_bytes(_image())
    (directory / "shot.0002.png").write_bytes(_image())
    result = verify_render_output(
        output_path=str(directory / "shot.%04d.png"), first_frame=1, last_frame=4, expected_format="png"
    )
    assert result["verified"] is False
    assert result["missing_frames"] == [3, 4]
    assert result["present_frame_count"] == 2
    assert "NOT native Nuke render acceptance" in result["scope"]


def test_nonempty_garbage_file_is_unreadable_not_verified(tmp_path):
    """A request can succeed while leaving a present, non-empty, unreadable file."""
    target = tmp_path / "final.png"
    target.write_bytes(b"NOT_AN_IMAGE" * 8)
    result = verify_render_output(output_path=str(target), first_frame=1, last_frame=1, expected_format="png")
    assert target.is_file() and target.stat().st_size > 0
    assert result["verified"] is False
    assert result["unreadable_frames"] == [1]
    assert result["detected_formats"] == {"1": "unrecognized"}
    assert "unreadable or wrong-format output files" in result["reason"]


def test_zero_byte_and_format_mismatch_are_separately_reported(tmp_path):
    directory = tmp_path / "renders"
    directory.mkdir()
    (directory / "shot.0001.png").write_bytes(b"")
    (directory / "shot.0002.png").write_bytes(_image("jpg"))
    result = verify_render_output(
        output_path=str(directory / "shot.%04d.png"), first_frame=1, last_frame=2, expected_format="png"
    )
    assert result["zero_byte_frames"] == [1]
    assert result["unreadable_frames"] == [2]
    assert result["present_frame_count"] == 2
    assert result["verified"] is False


def test_format_alias_and_magic_number_sniffing(tmp_path):
    target = tmp_path / "frame.jpeg"
    target.write_bytes(_image("jpg"))
    explicit = verify_render_output(output_path=str(target), first_frame=1, last_frame=1, expected_format="jpeg")
    sniffed = verify_render_output(output_path=str(target), first_frame=1, last_frame=1)
    assert explicit["binding"]["expected_format"] == "jpg"
    assert sniffed["format_check"] == "magic_number_sniffed"
    assert sniffed["detected_formats"] == {"1": "jpg"}
    # A JPEG signature is only a candidate format. No decoder is available here,
    # so content acceptance stays unknown and verified stays false.
    assert explicit["verified"] is False
    assert explicit["content_acceptance"] == "unknown"
    assert sniffed["verified"] is False
    assert sniffed["content_validation"] == "signature_only"
    with pytest.raises(ValueError, match="expected_format"):
        verify_render_output(output_path=str(target), first_frame=1, last_frame=1, expected_format="bmp")

    png = tmp_path / "frame.png"
    png.write_bytes(_image())
    positive = verify_render_output(output_path=str(png), first_frame=1, last_frame=1, expected_format="PNG")
    assert positive["binding"]["expected_format"] == "png"
    assert positive["verified"] is True
    assert positive["content_acceptance"] == "verified"


def test_literal_path_cannot_prove_a_multi_frame_request(tmp_path):
    target = tmp_path / "final.png"
    target.write_bytes(_image())
    result = verify_render_output(output_path=str(target), first_frame=1, last_frame=5)
    assert result["verified"] is False
    assert result["requested_frame_count"] == 5
    assert result["checked_frame_count"] == 1
    assert result["warnings"][0]["code"] == "single_frame_path_collapses_range"
    assert "cannot prove a multi-frame request" in result["reason"]


def test_negative_frames_step_and_hash_sequences(tmp_path):
    png_dir = tmp_path / "png"
    png_dir.mkdir()
    for name in ("shot.-002.png", "shot.0000.png", "shot.0002.png"):
        (png_dir / name).write_bytes(_image())
    printf = verify_render_output(
        output_path=str(png_dir / "shot.%04d.png"),
        first_frame=-2,
        last_frame=2,
        step=2,
        expected_format="png",
    )
    assert printf["expected_frames"] == [-2, 0, 2]
    assert printf["verified"] is True

    hash_dir = tmp_path / "hash"
    hash_dir.mkdir()
    for frame in (1001, 1002):
        (hash_dir / f"shot.{frame:04d}.png").write_bytes(_image())
    hashed = verify_render_output(output_path=str(hash_dir / "shot.####.png"), first_frame=1001, last_frame=1002)
    assert hashed["verified"] is True
    assert hashed["mode"] == "sequence"


def test_windows_backslash_normalization_and_case_insensitive_lookup(tmp_path):
    directory = tmp_path / "Renders"
    directory.mkdir()
    (directory / "shot.0001.png").write_bytes(_image())
    backslashed = str(directory / "shot.%04d.png").replace("/", "\\")
    normalized = verify_render_output(output_path=backslashed, first_frame=1, last_frame=1)
    assert normalized["verified"] is True
    if sys.platform == "win32":
        upper = backslashed.replace("Renders", "RENDERS")
        assert verify_render_output(output_path=upper, first_frame=1, last_frame=1)["verified"] is True


def test_frame_limit_and_invalid_requests_fail_closed(tmp_path):
    directory = tmp_path / "renders"
    directory.mkdir()
    result = verify_render_output(
        output_path=str(directory / "shot.%04d.png"),
        first_frame=1,
        last_frame=5,
        max_frames=2,
    )
    assert result["frame_limit_exceeded"] is True
    assert result["verified"] is False
    assert result["warnings"][-1]["code"] == "frame_limit_exceeded"
    with pytest.raises(ValueError, match="dynamic sequence basename"):
        verify_render_output(output_path=str(directory / "shot.[frame].%04d.png"), first_frame=1, last_frame=1)
    with pytest.raises(ValueError, match="step"):
        verify_render_output(output_path=str(directory / "shot.png"), first_frame=1, last_frame=1, step=0)
    with pytest.raises(ValueError, match="last_frame"):
        verify_render_output(output_path=str(directory / "shot.png"), first_frame=3, last_frame=2)


# --- Area 3: execution source / billing transparency ---------------------------


def test_no_cloud_integration_billing_or_api_key_contract_is_invented():
    report = describe_execution_sources()
    external = report["external_services"]
    assert external["cloud_model_integration"] == "none"
    assert external["provider"] == "none"
    assert external["billing"] == "not_applicable"
    assert external["byo_api_key"] == "not_applicable"
    assert external["paid_calls"] == "none"
    assert external["paid_calls_added_by_this_change"] is False
    assert report["no_cloud_integration"] is True
    assert report["no_billing_estimates"] is True
    assert report["no_api_keys"] is True
    assert report["native_host_verification"] == "not_performed"


def test_execution_is_local_and_no_network_or_paid_call_is_added():
    report = describe_execution_sources()
    assert report["local_host"]["present"] is True
    assert report["local_host"]["network_required"] is False
    native = report["optional_local_native_subprocess"]
    assert native["device"] == "cpu"
    assert native["network_calls_in_this_repo"] is False
    assert native["air_node_classes"]
    assert native["air_class_contract"] == "read_from_machine_learning_contract"


def test_no_credentials_environment_or_private_paths_are_returned(monkeypatch):
    secret = "DIAGNOSTICS_CANARY_9f4b3c_DO_NOT_LEAK"
    monkeypatch.setenv("DCC_MCP_NUKE_TEST_SECRET", secret)
    payload = json.dumps(
        {
            "dependencies": diagnose_dependencies(None, module_available=lambda _name: True),
            "sources": describe_execution_sources(),
        }
    )
    assert secret not in payload
    assert "DCC_MCP_NUKE_TEST_SECRET" not in payload
    for key in ("credentials_read", "environment_values_returned", "private_paths_returned"):
        assert json.loads(payload)["dependencies"]["safety"][key] is False
        assert json.loads(payload)["sources"][key] is False
