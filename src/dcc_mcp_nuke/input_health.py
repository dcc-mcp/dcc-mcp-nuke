"""Read-only, bounded input and plug-in checks for the active Nuke script."""

from __future__ import annotations

import math
import os
import re
from pathlib import Path
from statistics import median
from typing import Any

from dcc_mcp_nuke.file_references import list_file_references, sequence_files, sequence_pattern

_OUTPUT_CLASSES = {"Write", "DeepWrite", "WriteGeo"}


def check_input_health(
    nuke: Any,
    *,
    include_write_ranges: bool = False,
    required_node_classes: list[str] | None = None,
    max_references: int = 256,
    max_issues: int = 256,
    max_frames: int = 10000,
    max_files: int = 10000,
    small_frame_ratio: float = 0.1,
) -> dict[str, Any]:
    """Check input files and ranges without creating nodes or evaluating pixels."""
    for name, value, limit in (
        ("max_references", max_references, 1024),
        ("max_issues", max_issues, 1024),
        ("max_frames", max_frames, 100000),
        ("max_files", max_files, 100000),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= limit:
            raise ValueError(f"{name} must be an integer from 1 to {limit}")
    if not isinstance(include_write_ranges, bool):
        raise ValueError("include_write_ranges must be a boolean")
    if (
        isinstance(small_frame_ratio, bool)
        or not isinstance(small_frame_ratio, (float, int))
        or not math.isfinite(small_frame_ratio)
        or not 0 < small_frame_ratio < 1
    ):
        raise ValueError("small_frame_ratio must be between 0 and 1")
    requested = [] if required_node_classes is None else required_node_classes
    if (
        not isinstance(requested, list)
        or len(requested) > 256
        or any(
            not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name) for name in requested
        )
    ):
        raise ValueError("required_node_classes must be a bounded list of literal class names")

    issues = {"error": [], "warning": [], "info": []}
    issue_count = 0

    def report(severity, code, node=None, **details):
        nonlocal issue_count
        issue_count += 1
        if sum(len(group) for group in issues.values()) < max_issues:
            issues[severity].append({"code": code, "node": node, **details})

    root = nuke.root()
    root_range = _range(root, "first_frame", "last_frame")
    if root_range is None:
        raise ValueError("Root frame range is unavailable or invalid")
    nodes = list(nuke.allNodes(group=root, recurseGroups=True))
    by_name = {node.fullName(): node for node in nodes}
    write_ranges = []
    if include_write_ranges:
        for node in nodes:
            if node.Class() in _OUTPUT_CLASSES and _knob(node, "use_limit", False):
                bounds = _range(node, "first", "last")
                if bounds is not None:
                    write_ranges.append(bounds)
                else:
                    report("warning", "invalid_write_range", node.fullName())
    references = list_file_references(nuke, max_items=max_references)
    checked = 0
    for reference in references["references"]:
        if reference["class"] in _OUTPUT_CLASSES:
            continue
        checked += 1
        name = reference["node"]
        detail = {"knob": reference["knob"]}
        if reference["error"]:
            report("warning", "unreadable_reference", name, **detail)
            continue
        if not reference["raw_value"]:
            continue
        node = by_name[name]
        node_range = _range(node, "first", "last")
        if node_range and (node_range[0] > root_range[0] or node_range[1] < root_range[1]):
            report(
                "warning", "range_mismatch", name, node_range=list(node_range), root_range=list(root_range), **detail
            )
        expected_ranges = [root_range, *write_ranges]
        if node_range:
            expected_ranges.append(node_range)
        try:
            path = _sequence_path(reference)
            files = sequence_files(path, max_files=max_files)
            if not files:
                report("error", "missing", name, path=path, **detail)
                continue
            pattern = sequence_pattern(Path(path).name)
            if "frame" not in pattern.groupindex:
                if os.path.getsize(files[0]) == 0:
                    report("warning", "zero_byte", name, path=path, **detail)
                continue
            if reference["animated"]:
                report("warning", "animated_path_current_frame_only", name, **detail)
                continue
            if any(last - first + 1 > max_frames for first, last in expected_ranges):
                report("warning", "frame_limit_exceeded", name, max_frames=max_frames, **detail)
                continue
            expected = {frame for first, last in expected_ranges for frame in range(first, last + 1)}
            if len(expected) > max_frames:
                report("warning", "frame_limit_exceeded", name, max_frames=max_frames, **detail)
                continue
            sizes = {int(pattern.fullmatch(Path(file).name)["frame"]): os.path.getsize(file) for file in files}
            missing = expected.difference(sizes)
            if missing:
                report("error", "missing_frames", name, **_ranges(missing), **detail)
            zero = {frame for frame, size in sizes.items() if frame in expected and size == 0}
            if zero:
                report("warning", "zero_byte_frames", name, **_ranges(zero), **detail)
            ordered = sorted(sizes)
            small = set()
            for index, frame in enumerate(ordered):
                neighbours = [sizes[ordered[i]] for i in (index - 1, index + 1) if 0 <= i < len(ordered)]
                neighbours = [size for size in neighbours if size > 0]
                if (
                    frame in expected
                    and sizes[frame] > 0
                    and neighbours
                    and sizes[frame] < median(neighbours) * small_frame_ratio
                ):
                    small.add(frame)
            if small:
                report("warning", "small_frames", name, **_ranges(small), **detail)
        except (OSError, ValueError):
            report("warning", "file_check_incomplete", name, **detail)

    if references["truncated"]:
        report("warning", "reference_limit_exceeded", reference_count=references["reference_count"])
    present_classes = {str(node.Class()) for node in nodes}
    inventory_available = True
    try:
        inventory = nuke.plugins(nuke.ALL | nuke.REGISTERED)
        available = {Path(str(plugin).replace("\\", "/")).stem for plugin in inventory}
    except Exception:
        inventory_available = False
        available = set()
        report("warning", "plugin_inventory_unavailable")
    for node in nodes:
        if node.Class() in {"Unknown", "UnknownNode"}:
            report("error", "unknown_node_class", node.fullName())
    missing_classes = sorted(set(requested).difference(present_classes | available))
    for name in missing_classes:
        report(
            "error" if inventory_available else "warning",
            "missing_plugin" if inventory_available else "unverified_plugin",
            node_class=name,
        )
    return {
        "issues": issues,
        "issue_count": issue_count,
        "truncated": issue_count > max_issues or references["truncated"],
        "checked_references": checked,
        "reference_count": references["reference_count"],
        "root_range": list(root_range),
        "write_ranges": [list(bounds) for bounds in write_ranges],
        "plugin_inventory_available": inventory_available,
        "node_classes": sorted(present_classes)[:max_references],
        "node_classes_truncated": len(present_classes) > max_references,
        "required_classes_not_found": missing_classes,
        "scope": "active script and explicitly required classes; unloaded scripts are not inspected",
    }


def _knob(node, name, default=None):
    knob = node.knobs().get(name)
    return default if knob is None else knob.value()


def _range(node, first, last):
    values = (_knob(node, first), _knob(node, last))
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or int(value) != value
        for value in values
    ):
        return None
    return tuple(map(int, values)) if values[0] <= values[1] else None


def _sequence_path(reference):
    raw = reference["raw_value"].replace("\\", "/")
    evaluated = reference["evaluated_path"].replace("\\", "/")
    basename = raw.rsplit("/", 1)[-1]
    if "frame" in sequence_pattern(basename).groupindex:
        if any(character in basename for character in "[]$"):
            raise ValueError("dynamic sequence basename cannot be inspected exhaustively")
        return evaluated.rsplit("/", 1)[0] + "/" + basename if "/" in evaluated else basename
    return evaluated


def _ranges(frames):
    ordered = sorted(frames)
    ranges = []
    for frame in ordered:
        if ranges and frame == ranges[-1][1] + 1:
            ranges[-1][1] = frame
        else:
            ranges.append([frame, frame])
    return {"frame_count": len(ordered), "frame_ranges": ranges[:64], "ranges_truncated": len(ranges) > 64}
