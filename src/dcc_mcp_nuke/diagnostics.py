"""Read-only, secret-free diagnostics for the Nuke Install SOP.

Answers three questions a user has after following the installation guide:

1. Why are nodes or Python modules still missing after installing?
2. Did a single-frame or sequence render actually produce the requested files?
3. Where does work execute, and is there any cloud/billing/API-key surface?

Everything here is read-only. No dependency, node or plug-in is installed,
imported or loaded; no credentials or environment values are read or returned;
no private absolute path is exposed. ``verify_render_output`` only inspects
files that already exist on disk, so a passing check is never native Nuke
render acceptance and mock/fake/temp fixtures cannot prove a real render.

PNG outputs are validated against a strict, provable subset using only the
standard library: chunk lengths/CRCs, legal IHDR fields, required chunk
ordering and a bounded IDAT decompression. Unknown critical chunks and illegal
structures fail; legal features this validator cannot prove (indexed color,
interlacing, palettes, unknown ancillary chunks) are unknown. Neither is
reported as content-verified. Formats without a decoder here stay
signature-only with unknown content acceptance.
"""

from __future__ import annotations

import importlib.machinery
import itertools
import os
import posixpath
import re
import struct
import sys
import zipimport
import zlib
from pathlib import Path
from typing import Any, Callable

from dcc_mcp_nuke.file_references import sequence_pattern
from dcc_mcp_nuke.plugins import inspect_plugin_environment

# Stable magic-number prefixes identify a *candidate* format only. A signature
# by itself never proves that the file is a readable image.
_FORMATS = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "exr": (b"\x76\x2f\x31\x01",),
    "tif": (b"II*\x00", b"MM\x00*"),
    "dpx": (b"SDPX", b"XPDS"),
}
_FORMAT_ALIASES = {"jpeg": "jpg", "tiff": "tif"}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_PNG_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
_PNG_KNOWN_CRITICAL = (b"IHDR", b"PLTE", b"IDAT", b"IEND")
_PNG_MAX_PLTE_BYTES = 3 * 256
# PNG IHDR dimensions are unsigned 32-bit values, but the spec caps them at
# 2**31 - 1. 100000 is only this validator's local decode budget: a legal PNG
# above it is a capability limit (unknown), never an illegal file.
_PNG_SPEC_MAX_DIMENSION = (1 << 31) - 1
_PNG_LOCAL_MAX_DIMENSION = 100000
_CONTENT_DECODERS = ("png",)
_MAX_IMAGE_BYTES = 64 * 1024 * 1024
# Mirrors file_references._SEQUENCE so substitution stays consistent with the
# reused sequence_pattern() detector.
_FRAME_TOKEN = re.compile(r"%0?([1-9][0-9]?)?d|#+")
_MODULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")
_CLASS_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")
_MAX_PATH = 8192
_MAX_ITEMS = 256
_MAX_FRAMES = 100000


def _safe_module_probe(name: str) -> str:
    """Locate a top-level module spec without importing or executing any code.

    ``importlib.util.find_spec("parent.child")`` imports ``parent`` and runs
    its ``__init__.py``; a diagnosis must never execute the code it inspects.
    Dotted names are therefore reported as unknown. For a single-segment name
    only explicit standard filesystem/zip finders are consulted, bypassing
    path hooks and cached custom finders. A locatable spec
    means "discoverable", not "import tested": importing can still fail on a
    missing transitive dependency or on top-level code.
    """
    if "." in name:
        return "unknown"
    if name in sys.modules:
        return "discoverable" if sys.modules[name] is not None else "unknown"
    try:
        for finder in (importlib.machinery.BuiltinImporter, importlib.machinery.FrozenImporter):
            if finder.find_spec(name) is not None:
                return "discoverable"
        loaders = (
            (importlib.machinery.ExtensionFileLoader, importlib.machinery.EXTENSION_SUFFIXES),
            (importlib.machinery.SourceFileLoader, importlib.machinery.SOURCE_SUFFIXES),
            (importlib.machinery.SourcelessFileLoader, importlib.machinery.BYTECODE_SUFFIXES),
        )
        for entry in sys.path:
            if not isinstance(entry, str):
                continue
            if importlib.machinery.FileFinder(entry, *loaders).find_spec(name) is not None:
                return "discoverable"
            try:
                finder = zipimport.zipimporter(entry)
            except zipimport.ZipImportError:
                continue
            if hasattr(finder, "find_spec"):
                found = finder.find_spec(name) is not None
            else:  # Python 3.9 zipimporters predate find_spec().
                loader, namespace_paths = finder.find_loader(name)
                found = loader is not None or bool(namespace_paths)
            if found:
                return "discoverable"
        return "not_found"
    except Exception:
        return "unknown"


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

    # Python modules: discovery only. Never install, import or execute module
    # code. A caller-supplied lookup may assert importability; the default
    # probe only reports a locatable spec and never claims an import was tested.
    supplied = module_available is not None
    method = "caller_supplied_lookup" if supplied else "stdlib_spec_locate_no_import"
    module_report = []
    for name in modules:
        if supplied:
            try:
                present = bool(module_available(name))
                status = "importable" if present else "not_found"
                item_certainty = "caller_asserted" if present else "verified_unavailable"
                note = "The caller supplied this lookup; its certainty is asserted by the caller."
                repair = (
                    "No action needed."
                    if present
                    else "Install this module into Nuke's embedded interpreter (see install.md); "
                    "this report does not install or import modules."
                )
            except Exception:
                status, item_certainty = "unknown", "unknown"
                note = "The supplied lookup failed; availability is unknown."
                repair = "Confirm availability from Nuke's embedded interpreter; this report does not import modules."
        else:
            probe = _safe_module_probe(name)
            if probe == "discoverable":
                status = "discoverable"
                item_certainty = "verified_discoverable_not_import_tested"
                note = (
                    "A module spec is locatable, but the module was not imported; transitive dependencies "
                    "and top-level code are unverified."
                )
                repair = (
                    "A locatable spec only proves discoverability. If importing this module in Nuke's "
                    "embedded interpreter fails, check its transitive dependencies and the host/embedded "
                    "Python environment (see install.md). This report does not import or execute module "
                    "code, so importability is not confirmed."
                )
            elif probe == "not_found":
                # A miss by the standard finders is not global unavailability:
                # a custom finder, cached importer or host plug-in path could
                # still provide the module, and none of them may be executed.
                status, item_certainty = "not_found", "unknown"
                note = (
                    "No spec was located by the standard spec-locating finders without executing module code. "
                    "Custom import hooks, cached finders and host plug-in paths are not consulted, so availability "
                    "is unknown rather than globally confirmed unavailable."
                )
                repair = (
                    "Availability is unknown because custom finders were not consulted; confirm from Nuke's "
                    "embedded interpreter before installing (see install.md). This report does not import or "
                    "execute module code."
                )
            else:
                status, item_certainty = "unknown", "unknown"
                note = (
                    "Dotted names and transitive imports cannot be checked without executing parent package "
                    "code; availability is unknown."
                )
                repair = "Confirm availability from Nuke's embedded interpreter; this report does not import modules."
        module_report.append(
            {
                "module": name,
                "status": status,
                "certainty": item_certainty,
                "method": method,
                "import_tested": False,
                "certainty_note": note,
                "repair": repair,
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
        if entry["status"] in ("importable", "discoverable"):
            continue
        issues.append(
            {
                "stage": "python_module",
                "code": "missing_module" if entry["certainty"] == "verified_unavailable" else "module_check_unknown",
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
        "scope": "read-only post-install diagnosis; no install, import, load, host execution, credential or env access",
        "interpreter": interpreter,
        "modules": module_report,
        "module_probe": {
            "import_tested": False,
            "module_code_executed": False,
            "dotted_names": "unknown_without_execution",
            "transitive_dependencies": "not_checked",
            "custom_import_hooks": "not_consulted",
            "spec_locate_only": not supplied,
        },
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
            "module_code_executed": False,
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


def _validate_png(data: bytes) -> tuple[bool, str]:
    """Validate a strict, provable PNG subset using only the standard library.

    A magic number alone is never enough. Chunk lengths/CRCs, IHDR fields,
    chunk ordering and a bounded IDAT decompression are checked. Only a
    non-interlaced, non-palette image with compression/filter method 0 and no
    unvalidated chunk can be claimed decodable. Unknown critical chunks and
    illegal structures are hard failures; legal features this validator cannot
    prove (indexed color, interlacing, palettes, unknown ancillary chunks) are
    reported as unknown. Neither is content-verified. This proves decodability
    of the byte stream, not that Nuke rendered it or that pixels are correct.
    """
    if not data.startswith(_PNG_SIGNATURE):
        return False, "bad_signature"
    if len(data) < 8 + 12:
        return False, "truncated_chunk"
    offset = 8
    width = height = bit_depth = color_type = interlace = None
    idat = bytearray()
    saw_plte = saw_idat = saw_iend = False
    idat_closed = False
    unvalidated_ancillary = False
    while offset + 12 <= len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        if length > len(data) - offset - 12:
            return False, "truncated_chunk"
        payload = data[offset + 8 : offset + 8 + length]
        (crc,) = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != crc:
            return False, "crc_mismatch"
        if not all(65 <= byte <= 90 or 97 <= byte <= 122 for byte in kind):
            return False, "invalid_chunk_type"
        # An uppercase first type byte marks a critical chunk. Unknown critical
        # chunks must not be ignored; unknown ancillary chunks are legal but
        # outside the subset this validator can prove.
        critical = not (kind[0] & 0x20)
        if critical and kind not in _PNG_KNOWN_CRITICAL:
            return False, "unknown_critical_chunk"
        if kind == b"IHDR":
            if offset != 8 or width is not None or length != 13:
                return False, "invalid_ihdr"
            width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            if (
                width <= 0
                or height <= 0
                or width > _PNG_SPEC_MAX_DIMENSION
                or height > _PNG_SPEC_MAX_DIMENSION
                or color_type not in _PNG_CHANNELS
                or bit_depth not in _PNG_DEPTHS[color_type]
                or compression != 0
                or filter_method != 0
                or interlace not in (0, 1)
            ):
                return False, "invalid_ihdr"
        elif kind == b"PLTE":
            if saw_idat:
                return False, "non_consecutive_idat"
            if (
                width is None
                or saw_plte
                or length == 0
                or length % 3
                or length > _PNG_MAX_PLTE_BYTES
                or color_type in (0, 4)
                or (color_type == 3 and length > (1 << bit_depth) * 3)
            ):
                return False, "invalid_plte"
            saw_plte = True
        elif kind == b"IDAT":
            if width is None:
                return False, "invalid_chunk_order"
            if idat_closed:
                return False, "non_consecutive_idat"
            idat.extend(payload)
            saw_idat = True
        elif kind == b"IEND":
            if length != 0 or not saw_idat:
                return False, "invalid_iend"
            saw_iend = True
            offset += 12 + length
            break
        else:
            unvalidated_ancillary = True
            # Ancillary chunks may follow the final IDAT, but IDAT cannot resume.
            idat_closed = saw_idat
        offset += 12 + length
    if width is None or not idat or not saw_iend or offset != len(data):
        return False, "missing_required_chunks"
    # Spec-legal dimensions above the local decode budget are a capability
    # limit, not an illegal IHDR. Return before computing row_bytes/expected so
    # a huge-but-legal image cannot inflate the decode budget arithmetic.
    if width > _PNG_LOCAL_MAX_DIMENSION or height > _PNG_LOCAL_MAX_DIMENSION:
        return False, "unknown:dimensions_exceed_local_validation_budget"
    if interlace == 1:
        return False, "unknown:interlaced_not_validated"
    # Indexed color requires PLTE. Even with a legal palette, palette index
    # semantics are not decoded here, so this subset stays unknown.
    if color_type == 3 and not saw_plte:
        return False, "missing_required_palette"
    row_bytes = (width * _PNG_CHANNELS[color_type] * bit_depth + 7) // 8
    expected = height * (row_bytes + 1)
    if expected > _MAX_IMAGE_BYTES:
        return False, "unknown:idat_exceeds_validation_budget"
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(bytes(idat), expected + 1)
    except (zlib.error, MemoryError, ValueError):
        return False, "idat_not_decodable"
    if len(raw) != expected or not decoder.eof:
        return False, "idat_size_mismatch"
    if any(raw[row * (row_bytes + 1)] > 4 for row in range(height)):
        return False, "invalid_filter_type"
    if color_type == 3:
        return False, "unknown:indexed_color_not_validated"
    if saw_plte:
        return False, "unknown:palette_chunk_not_validated"
    if unvalidated_ancillary:
        return False, "unknown:ancillary_chunk_not_validated"
    return True, "decodable"


def _failure_reason(missing, zero, unreadable, content_unverified, frame_limit_exceeded, collapse_warning) -> str:
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
    if content_unverified:
        parts.append("image content could not be decode-validated with available decoders")
    return "; ".join(parts) if parts else "no expected frames to check"


def resolve_output_format(file_type: Any, output_path: Any) -> str | None:
    """Resolve a provable output image format for ``verify_render_output``.

    Binds a Write node's ``file_type`` when it names a supported image format,
    or a well-founded path extension when ``file_type`` is empty (the real
    Write contract). Existing format aliases are honored. A canonical format
    name is returned only when the format is unambiguous; every unclear case
    (a named but unsupported ``file_type``, contradictory signals, an absent or
    unrecognized extension) returns ``None`` so the caller must stay
    ``unknown`` rather than fall back to unrestricted magic-number sniffing.
    No file is opened and no image is decoded here.
    """

    def canonical(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        raw = value.strip().lower().lstrip(".")
        if not raw:
            return None
        fmt = _FORMAT_ALIASES.get(raw, raw)
        return fmt if fmt in _FORMATS else None

    ft_named = isinstance(file_type, str) and bool(file_type.strip())
    ft = canonical(file_type)
    suffix = ""
    if isinstance(output_path, str):
        normalized = output_path.replace("\\", "/")
        suffix = posixpath.splitext(posixpath.basename(normalized))[1]
    pt = canonical(suffix)

    # A named but unsupported/unrecognized file_type (e.g. "mov") cannot be
    # bound to a verifier signature: stay unknown.
    if ft_named and ft is None:
        return None
    # Contradictory explicit signals cannot be resolved without host knowledge.
    if ft is not None and pt is not None and ft != pt:
        return None
    # An explicit, supported file_type is authoritative, including when the
    # path has no usable extension.
    if ft is not None:
        return ft
    # Empty file_type: infer from a well-founded path extension. An absent or
    # unrecognized extension is unknown, never "unrestricted format".
    return pt


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

    Confirms actual output existence, the expected frame set, format binding
    and (for PNG) a file decodable within the strict validated subset. Illegal
    PNG structures fail; legal features outside that subset stay unknown.
    Formats with no decoder here are signature-only: their content acceptance
    stays unknown and ``verified`` stays false. A native ``nuke.execute``
    returning is not treated as proof.
    ``content_validation``/``content_acceptance`` are group-scoped over the
    full requested frame set: ``full_decode``/``verified`` require a decodable
    content check for every required frame, so a missing or empty output can
    never make the group look content-verified. A frame set truncated by
    ``max_frames`` or collapsed by a literal path is only partially checked,
    so the group stays unverified too.
    This is a filesystem readback only; it is never native Nuke render
    acceptance and mock/temp fixtures do not prove a render.
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

    # Count arithmetically before allocating: len(range(...)) overflows for
    # very large bounds, and list(range(...)) materializes every frame before
    # max_frames is applied. islice keeps materialization bounded to max_frames.
    requested_count = (last_frame - first_frame) // step + 1
    frame_limit_exceeded = requested_count > max_frames
    requested_range = range(first_frame, last_frame + 1, step)
    requested_frames = list(itertools.islice(requested_range, max_frames))
    # A literal path (no frame token) collapses a multi-frame range into one
    # file; only the final frame can survive. Compute that frame arithmetically:
    # last_frame is the requested bound, not necessarily a rendered frame when
    # step > 1 (e.g. first=1, last=2, step=2 renders only frame 1).
    final_frame = first_frame + (requested_count - 1) * step
    checked_frames = [final_frame] if not is_sequence else requested_frames
    collapse_warning = not is_sequence and requested_count > 1

    present, missing, zero, unreadable, detected = [], [], [], [], {}
    content_unverified, content_checks = [], {}
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
            size = os.path.getsize(path)
            if size <= 0:
                present.append(frame)
                zero.append(frame)
                continue
            with open(path, "rb") as handle:
                # Read the signature first. Only a format that has a decoder
                # here, and only within the byte budget, needs the remaining
                # contents; signature-only formats never read their body.
                signature = handle.read(8)
                kind = _sniff(signature)
                if kind in _CONTENT_DECODERS and size <= _MAX_IMAGE_BYTES:
                    data = signature + handle.read(_MAX_IMAGE_BYTES + 1 - len(signature))
                else:
                    data = signature
        except OSError:
            missing.append(frame)
            continue
        present.append(frame)
        detected[str(frame)] = kind or "unrecognized"
        if (fmt is not None and kind != fmt) or (fmt is None and kind is None):
            unreadable.append(frame)
            content_checks[str(frame)] = "not_checked:format_mismatch_or_unrecognized"
            continue
        if size > _MAX_IMAGE_BYTES:
            content_unverified.append(frame)
            content_checks[str(frame)] = "unknown:exceeds_validation_budget"
            continue
        if kind in _CONTENT_DECODERS:
            decodable, detail = _validate_png(data)
            if decodable:
                content_checks[str(frame)] = "decodable"
            elif detail.startswith("unknown:"):
                # A decoder capability limit is not proof of a broken file.
                content_unverified.append(frame)
                content_checks[str(frame)] = detail
            else:
                unreadable.append(frame)
                content_checks[str(frame)] = "invalid:" + detail
            continue
        # No decoder is available for this format. A matching signature proves
        # only a candidate format; content readability stays unknown.
        content_unverified.append(frame)
        content_checks[str(frame)] = "signature_only"

    # content_checks records only frames that were present and non-empty; a
    # missing or zero-byte frame continues without an entry. Requiring one
    # check per checked frame prevents all([]) (all missing) or a partial
    # all-decodable set (some missing) from being read as a full decode.
    # ``checked_frames`` can also be a strict subset of the requested frame
    # set: max_frames truncates the range, and a literal (frame-token-free)
    # path collapses a multi-frame request to a single file. In either case
    # the group was only partially checked, so a group-scoped
    # full_decode/verified claim would be dishonest even when every checked
    # frame decodes. Gate the claim on the WHOLE requested frame set having
    # been checked.
    full_frame_set_checked = len(checked_frames) == requested_count
    content_checks_complete = len(content_checks) == len(checked_frames)
    fully_decoded = (
        bool(checked_frames)
        and full_frame_set_checked
        and content_checks_complete
        and not content_unverified
        and all(value == "decodable" for value in content_checks.values())
    )
    if fully_decoded:
        content_validation, content_acceptance = "full_decode", "verified"
    elif unreadable:
        content_validation, content_acceptance = "decode_failed", "failed"
    elif content_unverified:
        content_validation, content_acceptance = "signature_only", "unknown"
    else:
        content_validation, content_acceptance = "not_performed", "unknown"
    verified = (
        bool(checked_frames)
        and not frame_limit_exceeded
        and not collapse_warning
        and not (missing or zero or unreadable or content_unverified)
        and fully_decoded
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
        "requested_frame_count": requested_count,
        "expected_frames": checked_frames[:64],
        "expected_frames_truncated": len(checked_frames) > 64,
        "checked_frame_count": len(checked_frames),
        "present_frames": present[:64],
        "present_frame_count": len(present),
        "missing_frames": missing[:64],
        "missing_frame_count": len(missing),
        "zero_byte_frames": zero[:64],
        "unreadable_frames": unreadable[:64],
        "content_unverified_frames": content_unverified[:64],
        "content_checks": dict(list(content_checks.items())[:64]),
        "content_decoders": list(_CONTENT_DECODERS),
        "content_validation": content_validation,
        "content_acceptance": content_acceptance,
        "detected_formats": dict(list(detected.items())[:64]),
        "format_check": "requested_magic_number_verified" if fmt else "magic_number_sniffed",
        "format_check_note": "Magic numbers identify a candidate format only; content acceptance is reported separately.",
        "warnings": warnings,
        "frame_limit_exceeded": frame_limit_exceeded,
        "max_frames": max_frames,
        "reason": "all expected frames present, nonempty and readable"
        if verified
        else _failure_reason(missing, zero, unreadable, content_unverified, frame_limit_exceeded, collapse_warning),
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
