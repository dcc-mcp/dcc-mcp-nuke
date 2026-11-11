"""Read-only, secret-free diagnostics for the Nuke Install SOP.

Answers three questions a user has after following the installation guide:

1. Why are nodes or Python modules still missing after installing?
2. Did a single-frame or sequence render actually produce the requested files?
3. Where does work execute, and is there any cloud/billing/API-key surface?

Everything here is read-only. No dependency, node or plug-in is installed or
loaded; no credentials or environment values are read or returned; no private
absolute path is exposed. ``verify_render_output`` only inspects files that
already exist on disk, so a passing check is never native Nuke render
acceptance and mock/fake/temp fixtures cannot prove a real render.
"""

from __future__ import annotations

import importlib.util
import os
import posixpath
import re
import sys
from pathlib import Path
from typing import Any, Callable

from dcc_mcp_nuke.file_references import sequence_pattern
from dcc_mcp_nuke.plugins import inspect_plugin_environment

# Stable magic-number prefixes. These prove a file is a readable image rather
# than merely a present, non-empty path left behind by an aborted render.
_FORMATS = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "exr": (b"\x76\x2f\x31\x01",),
    "tif": (b"II*\x00", b"MM\x00*"),
    "dpx": (b"SDPX", b"XPDS"),
}
_FORMAT_ALIASES = {"jpeg": "jpg", "tiff": "tif"}
# Mirrors file_references._SEQUENCE so substitution stays consistent with the
# reused sequence_pattern() detector.
_FRAME_TOKEN = re.compile(r"%0?([1-9][0-9]?)?d|#+")
_MODULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")
_CLASS_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")
_MAX_PATH = 8192
_MAX_ITEMS = 256
_MAX_FRAMES = 100000


def _default_module_available(name: str) -> bool:
    """Locate a module without importing its top-level code or installing it."""
    return importlib.util.find_spec(name) is not None


def _validate_list(label: str, value: Any, pattern: re.Pattern) -> list[str]:
    if value is None:
        return []
    if (
        not isinstance(value, list)
        or len(value) > _MAX_ITEMS
        or any(not isinstance(item, str) or not pattern.fullmatch(item) for item in value)
    ):
        raise ValueError(f"{label} must be a bounded list of literal identifiers")
    return list(dict.fromkeys(value))


def diagnose_dependencies(
    nuke: Any,
    *,
    required_modules: list[str] | None = None,
    required_node_classes: list[str] | None = None,
    module_available: Callable[[str], bool] | None = None,
    max_items: int = 128,
) -> dict[str, Any]:
    """Read-only post-install diagnosis; never installs, loads or prints secrets.

    Each issue records a ``stage``, known ``facts``, a ``certainty`` that is
    explicit about what could not be verified, and a minimal ``repair`` hint.
    An insufficient Nuke version is never inferred without a supported
    contract, and a third-party ComfyUI report is never treated as the cause
    of a dcc-mcp-nuke defect.
    """
    if isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= _MAX_ITEMS:
        raise ValueError("max_items must be an integer from 1 to 256")
    modules = _validate_list("required_modules", required_modules, _MODULE_NAME)
    classes = _validate_list("required_node_classes", required_node_classes, _CLASS_NAME)

    # Interpreter provenance: distinguish Nuke's embedded interpreter from an
    # external CPython (the usual cause of "installed but won't import"). Only
    # the executable basename is exposed, never a private absolute path.
    loaded_nuke = sys.modules.get("nuke")
    in_host = nuke is not None and loaded_nuke is nuke
    version = getattr(nuke, "NUKE_VERSION_STRING", None) if nuke is not None else None
    version = version[:64] if isinstance(version, str) else None
    executable_name = Path(sys.executable).name
    if in_host and version:
        context, certainty = "nuke_embedded", "verified"
    elif loaded_nuke is None and executable_name.lower().startswith("python"):
        context, certainty = "external_cpython", "verified"
    else:
        context, certainty = "unknown", "unknown"
    interpreter = {
        "context": context,
        "certainty": certainty,
        "facts": {
            "nuke_module_loaded_in_this_process": loaded_nuke is not None,
            "nuke_version_string": version,
            "sys_executable_name": executable_name,
            "python_version": sys.version.split()[0],
            "platform": sys.platform,
        },
        "repair": (
            "Run this check from Nuke's embedded interpreter, or reinstall with '--python' pointing at "
            "Nuke's embedded executable; see install.md."
            if context == "external_cpython"
            else "No interpreter change needed."
            if context == "nuke_embedded"
            else "Interpreter provenance is indeterminate; confirm the Nuke embedded Python used by install."
        ),
    }

    # Python modules: availability only. Never auto-install, never read secrets.
    lookup = module_available or _default_module_available
    method = "caller_supplied_lookup" if module_available is not None else "find_spec_locate_only"
    module_report = []
    for name in modules:
        try:
            present = bool(lookup(name))
            status, item_certainty = ("importable", "verified") if present else ("not_found", "verified_unavailable")
        except Exception:
            status, item_certainty = "unknown", "unknown"
        module_report.append(
            {
                "module": name,
                "status": status,
                "certainty": item_certainty,
                "method": method,
                "repair": (
                    "No action needed."
                    if status == "importable"
                    else "Install this module into Nuke's embedded interpreter (see install.md); "
                    "this report does not install modules."
                ),
            }
        )

    # Third-party nodes / OFX / gizmos: reuse the existing read-only inventory.
    node_report = []
    plugin_kinds = []
    inventory_available = False
    if classes:
        try:
            inventory = inspect_plugin_environment(nuke, required_node_classes=classes, max_items=max_items)
            inventory_available = True
            certainty_by_status = {
                "registered": "verified",
                "not_registered": "verified_unavailable",
                "discoverable_candidate": "unknown",
                "unverified": "unknown",
            }
            for item in inventory.get("requirements", []):
                state = str(item.get("status", "unverified"))
                node_report.append(
                    {
                        "stage": "third_party_nodes",
                        "code": "node_class_" + state,
                        "subject": item.get("node_class"),
                        "certainty": certainty_by_status.get(state, "unknown"),
                        "facts": {
                            "status": state,
                            "runtime_validation": item.get("runtime_validation", "not_run"),
                        },
                        "repair": (
                            "No action needed."
                            if state == "registered"
                            else "Place the node's plug-in (NDK .so/.dll, OFX bundle, or .gizmo) on a Nuke "
                            "plugin path and reload; this report does not install or load it."
                        ),
                    }
                )
            for key in ("available_plugins", "loaded_plugins", "task_assets"):
                for asset in inventory.get(key, []):
                    kind = asset.get("kind") if isinstance(asset, dict) else None
                    if isinstance(kind, str):
                        plugin_kinds.append(kind)
        except Exception:
            inventory_available = False

    issues = []
    for entry in module_report:
        if entry["status"] != "importable":
            issues.append(
                {
                    "stage": "python_module",
                    "code": "missing_module" if entry["status"] == "not_found" else "module_check_unknown",
                    "subject": entry["module"],
                    "certainty": entry["certainty"],
                    "facts": {"method": entry["method"]},
                    "repair": entry["repair"],
                }
            )
    issues.extend(node_report)
    if classes and not inventory_available:
        issues.append(
            {
                "stage": "third_party_nodes",
                "code": "plugin_inventory_unavailable",
                "subject": None,
                "certainty": "unknown",
                "facts": {},
                "repair": "Plugin inventory could not be read; node availability is unknown, not confirmed missing.",
            }
        )
    counts = {"verified": 0, "verified_unavailable": 0, "unknown": 0}
    for issue in issues:
        counts[issue["certainty"]] += 1

    return {
        "scope": "read-only post-install diagnosis; no install, load, host execution, credential or env access",
        "interpreter": interpreter,
        "modules": module_report,
        "node_classes": node_report,
        "plugin_kinds_observed": sorted(set(plugin_kinds)),
        "plugin_inventory_available": inventory_available,
        "version": {
            "observed": version or "unknown",
            "minimum_required_contract": "unknown",
            "insufficiency_verdict": "unknown",
            "note": "No supported minimum-version contract for arbitrary third-party nodes is declared in this "
            "repository; an insufficient Nuke version is never inferred without one.",
        },
        "comfyui": {
            "applicable": False,
            "verdict": "not_applicable",
            "reason": "This repository contains no ComfyUI bridge or integration. Third-party reports about an "
            "old ComfyUI version are leads, not a verified cause of a dcc-mcp-nuke defect; no causal claim "
            "is made and no such integration is created.",
        },
        "existing_capabilities": {
            "input_health": {
                "tool": "nuke_input_health__check_input_health",
                "covers": [
                    "missing",
                    "missing_frames",
                    "zero_byte",
                    "zero_byte_frames",
                    "small_frames",
                    "range_mismatch",
                    "write_range_mismatch",
                    "unknown_node_class",
                    "missing_plugin",
                ],
            },
            "plugin_inventory": {
                "tool": "nuke_plugins__inspect_plugin_environment",
                "covers": ["registered_node_classes", "ofx", "gizmo", "native", "plugin_paths"],
            },
            "preview": {
                "tool": "nuke_preview__render_node_preview",
                "covers": ["single-frame PNG render and real PNG readback"],
            },
        },
        "issues": issues,
        "issue_counts": counts,
        "safety": {
            "credentials_read": False,
            "credentials_printed": False,
            "auto_install_performed": False,
            "environment_values_returned": False,
            "private_paths_returned": False,
            "host_api_used": "read_only_plugin_inventory" if classes else "none",
        },
    }


def _frame_filename(match: re.Match, frame: int) -> str:
    token = match.group()
    width = len(token) if token.startswith("#") else int(match.group(1) or 0)
    if frame >= 0:
        return str(frame).zfill(width)
    return "-" + str(-frame).zfill(max(1, width - 1))


def _sniff(head: bytes) -> str | None:
    for name, signatures in _FORMATS.items():
        if any(head.startswith(signature) for signature in signatures):
            return name
    return None


def _failure_reason(missing, zero, unreadable, frame_limit_exceeded, collapse_warning) -> str:
    parts = []
    if collapse_warning:
        parts.append("single-frame output path cannot prove a multi-frame request")
    if frame_limit_exceeded:
        parts.append("expected frame set exceeds max_frames")
    if missing:
        parts.append("missing output files")
    if zero:
        parts.append("zero-byte output files")
    if unreadable:
        parts.append("unreadable or wrong-format output files")
    return "; ".join(parts) if parts else "no expected frames to check"


def verify_render_output(
    *,
    output_path: str,
    first_frame: int,
    last_frame: int,
    step: int = 1,
    expected_format: str | None = None,
    input_path: str | None = None,
    max_frames: int = 10000,
) -> dict[str, Any]:
    """Prove a render's real output files, not merely that execute() returned.

    Confirms actual output existence, the expected frame set, a readable image
    format and binding to the request. A native ``nuke.execute`` returning is
    not treated as proof. This is a filesystem readback only; it is never
    native Nuke render acceptance and mock/temp fixtures do not prove a render.
    """
    for name, value in (
        ("first_frame", first_frame),
        ("last_frame", last_frame),
        ("step", step),
        ("max_frames", max_frames),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer")
    if step < 1:
        raise ValueError("step must be greater than or equal to 1")
    if last_frame < first_frame:
        raise ValueError("last_frame must be greater than or equal to first_frame")
    if not 1 <= max_frames <= _MAX_FRAMES:
        raise ValueError("max_frames must be between 1 and 100000")
    if not isinstance(output_path, str) or not output_path or len(output_path) > _MAX_PATH or "\x00" in output_path:
        raise ValueError("output_path must be a bounded literal path")
    if input_path is not None and (not isinstance(input_path, str) or len(input_path) > _MAX_PATH):
        raise ValueError("input_path must be a bounded literal path or None")
    if expected_format is not None:
        if not isinstance(expected_format, str):
            raise ValueError("expected_format must be a supported image format or None")
        raw = expected_format.lower().lstrip(".")
        fmt = _FORMAT_ALIASES.get(raw, raw)
        if fmt not in _FORMATS:
            raise ValueError("expected_format must be one of png, jpg, exr, tif, dpx or None")
    else:
        fmt = None

    normalized = output_path.replace("\\", "/")
    directory, basename = posixpath.split(normalized)
    is_sequence = "frame" in sequence_pattern(basename).groupindex
    if is_sequence and any(character in basename for character in "[]$"):
        raise ValueError("dynamic sequence basename cannot be verified exhaustively")
    mode = "sequence" if is_sequence else "single_frame"

    requested_range = range(first_frame, last_frame + 1, step)
    requested_frames = list(requested_range)
    frame_limit_exceeded = len(requested_frames) > max_frames
    if frame_limit_exceeded:
        requested_frames = requested_frames[:max_frames]
    # A literal path (no frame token) collapses a multi-frame range into one
    # file; only the final frame can survive. Report this instead of claiming
    # that every requested frame was written.
    checked_frames = [last_frame] if not is_sequence else requested_frames
    collapse_warning = not is_sequence and len(requested_range) > 1

    present, missing, zero, unreadable, detected = [], [], [], [], {}
    for frame in checked_frames:
        name = (
            basename
            if not is_sequence
            else _FRAME_TOKEN.sub(lambda match, bound=frame: _frame_filename(match, bound), basename)
        )
        path = posixpath.join(directory, name) if directory else name
        try:
            if not os.path.isfile(path):
                missing.append(frame)
                continue
            if os.path.getsize(path) <= 0:
                present.append(frame)
                zero.append(frame)
                continue
            with open(path, "rb") as handle:
                head = handle.read(8)
        except OSError:
            missing.append(frame)
            continue
        present.append(frame)
        kind = _sniff(head)
        detected[str(frame)] = kind or "unrecognized"
        if fmt is not None and kind != fmt:
            unreadable.append(frame)
        elif fmt is None and kind is None:
            unreadable.append(frame)

    verified = (
        bool(checked_frames)
        and not frame_limit_exceeded
        and not collapse_warning
        and not (missing or zero or unreadable)
    )
    warnings = []
    if collapse_warning:
        warnings.append(
            {
                "code": "single_frame_path_collapses_range",
                "detail": "The output path has no frame token; a multi-frame render writes repeatedly to one "
                "file, so only the final frame can be proven and no frame set is distinguishable.",
            }
        )
    if frame_limit_exceeded:
        warnings.append({"code": "frame_limit_exceeded", "max_frames": max_frames})

    return {
        "verified": verified,
        "mode": mode,
        "binding": {
            "output_path": output_path,  # caller-supplied request data, not a discovered private path
            "input_path": input_path,
            "first_frame": first_frame,
            "last_frame": last_frame,
            "step": step,
            "expected_format": fmt,
        },
        "requested_frame_count": len(requested_range),
        "expected_frames": checked_frames[:64],
        "expected_frames_truncated": len(checked_frames) > 64,
        "checked_frame_count": len(checked_frames),
        "present_frames": present[:64],
        "present_frame_count": len(present),
        "missing_frames": missing[:64],
        "missing_frame_count": len(missing),
        "zero_byte_frames": zero[:64],
        "unreadable_frames": unreadable[:64],
        "detected_formats": dict(list(detected.items())[:64]),
        "format_check": "requested_magic_number_verified" if fmt else "magic_number_sniffed",
        "warnings": warnings,
        "frame_limit_exceeded": frame_limit_exceeded,
        "max_frames": max_frames,
        "reason": "all expected frames present, nonempty and readable"
        if verified
        else _failure_reason(missing, zero, unreadable, frame_limit_exceeded, collapse_warning),
        "scope": "filesystem readback only; NOT native Nuke render acceptance. Mock/fake/temp fixtures prove "
        "the verifier, not a real Nuke render.",
        "native_render_acceptance": "not_performed",
    }


def describe_execution_sources() -> dict[str, Any]:
    """Static, secret-free provenance of where this adapter's work runs.

    States the local host and optional local native subprocess, and explicitly
    records that this repository has no cloud model, provider, billing or
    API-key surface. Nothing is fabricated, no paid call is added, and no
    credential, environment value or private path is read or returned.
    """
    try:
        from dcc_mcp_nuke.machine_learning import AIR_CLASSES

        air_classes = list(AIR_CLASSES)
        air_contract = "read_from_machine_learning_contract"
    except Exception:
        air_classes = []
        air_contract = "unknown (AIR class contract could not be read)"
    return {
        "scope": "static contract description; no execution, no paid calls, and no credential, environment "
        "value or private path is read or returned",
        "local_host": {
            "present": True,
            "execution_location": "the live Nuke process (embedded Python interpreter)",
            "description": "Typed skills are dispatched locally into Nuke's main thread through the in-host "
            "MCP server or dcc-mcp-cli.",
            "network_required": False,
        },
        "optional_local_native_subprocess": {
            "present": bool(air_classes),
            "execution_location": "an owned, time-limited local NukeX subprocess",
            "description": "Foundry AIR machine-learning nodes can be trained or converted in a bounded local "
            "process on CPU.",
            "air_node_classes": air_classes,
            "air_class_contract": air_contract,
            "device": "cpu",
            "network_calls_in_this_repo": False,
        },
        "external_services": {
            "cloud_model_integration": "none",
            "provider": "none",
            "billing": "not_applicable",
            "billing_estimate": "not_applicable: no pricing, metering or cost model exists in this repository",
            "api_key": "not_applicable: no key is requested, stored, read or required by this adapter",
            "byo_api_key": "not_applicable",
            "paid_calls": "none",
            "paid_calls_added_by_this_change": False,
        },
        "no_cloud_integration": True,
        "no_billing_estimates": True,
        "no_api_keys": True,
        "native_host_verification": "not_performed",
        "unknowns": [
            "The availability and terms of a specific Nuke/NukeX license are not verified here.",
            "Any external service, provider, pricing or credential handling belongs to the surrounding MCP "
            "host/CLI, outside this repository's contracts; its details are unknown to this adapter.",
        ],
        "credentials_read": False,
        "environment_values_returned": False,
        "private_paths_returned": False,
    }
