"""Bounded file-reference discovery and transactional path changes for Nuke."""

from __future__ import annotations

import ntpath
import os
import posixpath
import re
from typing import Any

_RELATIVE = "[file dirname [value root.name]]/"
_SEQUENCE = re.compile(r"%0?([1-9][0-9]?)?d|#+")
_MAX_ITEMS = 1024
_MAX_PATH = 8192


class FileReferenceError(Exception):
    """Public-safe file-reference contract failure."""


class FileReferenceValidationError(FileReferenceError, ValueError):
    """A caller supplied an unsupported or out-of-range value."""


class FileReferenceRuntimeError(FileReferenceError, RuntimeError):
    """The host could not prove the requested postcondition."""


def list_file_references(nuke: Any, *, max_items: int = 256, node_classes: list[str] | None = None) -> dict[str, Any]:
    """Inspect every File_Knob, including nodes nested inside groups and gizmos."""
    records, count = _references(nuke, max_items, node_classes)
    return {
        "references": [record for record, _knob in records],
        "reference_count": count,
        "truncated": count > max_items,
    }


def path_exists(path: str) -> bool:
    """Whether a file, or at least one frame of a printf/hash sequence, exists."""
    if not path:
        return False
    path = path.replace("\\", "/")
    _directory, basename = posixpath.split(path)
    if not _SEQUENCE.search(basename):
        return os.path.isfile(path)
    return bool(sequence_files(path, max_files=100000))


def reference_path(reference: dict[str, Any]) -> str:
    """Keep sequence tokens while using Nuke's evaluated directory substitutions."""
    raw = (reference.get("raw_value") or "").replace("\\", "/")
    evaluated = (reference.get("evaluated_path") or raw).replace("\\", "/")
    basename = posixpath.basename(raw)
    if _SEQUENCE.search(basename) and not _expression(basename):
        return posixpath.join(posixpath.dirname(evaluated), basename)
    return evaluated


def sequence_files(path: str, *, max_files: int = 10000) -> list[str]:
    """Resolve concrete/printf/hash paths with a cap on inspected directory entries."""
    _bounded_int("max_files", max_files, 1, 100000)
    path = path.replace("\\", "/")
    directory, basename = posixpath.split(path)
    if not _SEQUENCE.search(basename):
        return [path] if os.path.isfile(path) else []
    pattern = sequence_pattern(basename)
    try:
        with os.scandir(directory or ".") as entries:
            files = []
            for count, entry in enumerate(entries, 1):
                if count > max_files:
                    raise FileReferenceValidationError("sequence directory exceeds max_files")
                if entry.is_file() and pattern.fullmatch(entry.name):
                    files.append(entry.path.replace("\\", "/"))
            return sorted(files)
    except OSError:
        return []


def remap_path_prefixes(
    nuke: Any,
    mappings: list[dict[str, str]],
    *,
    dry_run: bool = True,
    only_existing: bool = False,
    node_classes: list[str] | None = None,
    max_items: int = 256,
) -> dict[str, Any]:
    """Replace path roots at directory boundaries; longest matching root wins."""
    _boolean("only_existing", only_existing)
    if not isinstance(mappings, list) or not 1 <= len(mappings) <= 32:
        raise FileReferenceValidationError("mappings must contain 1 to 32 prefix pairs")
    prefixes: dict[str, str] = {}
    for mapping in mappings:
        if not isinstance(mapping, dict) or set(mapping) != {"old_prefix", "new_prefix"}:
            raise FileReferenceValidationError("each mapping requires only old_prefix and new_prefix")
        old = _literal_path("old_prefix", mapping["old_prefix"], absolute=True).rstrip("/")
        new = _literal_path("new_prefix", mapping["new_prefix"], absolute=True).rstrip("/")
        if old in prefixes:
            raise FileReferenceValidationError("old_prefix values must be unique")
        prefixes[old] = new
    ordered = sorted(prefixes, key=len, reverse=True)

    def propose(record):
        raw = record["raw_value"].replace("\\", "/")
        for old in ordered:
            if raw == old or raw.startswith(old + "/"):
                candidate = prefixes[old] + raw[len(old) :]
                if only_existing and not path_exists(candidate):
                    return None, "destination_missing"
                return candidate, None
        return None, "no_prefix_match"

    return _change_paths(nuke, propose, dry_run, node_classes, max_items)


def convert_file_paths(
    nuke: Any,
    mode: str,
    *,
    dry_run: bool = True,
    node_classes: list[str] | None = None,
    max_items: int = 256,
) -> dict[str, Any]:
    """Convert paths under the saved script folder to/from Nuke-relative form."""
    if mode not in ("relative", "absolute"):
        raise FileReferenceValidationError("mode must be relative or absolute")
    script_name = _literal_path("saved script name", nuke.root().name(), absolute=True)
    path_module = ntpath if ntpath.splitdrive(script_name)[0] else posixpath
    folder = path_module.dirname(script_name)

    def propose(record):
        raw = record["raw_value"].replace("\\", "/")
        if mode == "absolute":
            if not raw.startswith(_RELATIVE):
                return None, "not_script_relative"
            tail = _literal_path("relative path", raw[len(_RELATIVE) :])
            candidate = path_module.normpath(path_module.join(folder, tail)).replace("\\", "/")
        else:
            if not _absolute(raw):
                return None, "not_absolute"
            try:
                relative = path_module.relpath(raw, folder).replace("\\", "/")
            except ValueError:
                return None, "outside_script_folder"
            if relative == ".." or relative.startswith("../"):
                return None, "outside_script_folder"
            candidate = _RELATIVE + relative
        return candidate, None

    return _change_paths(nuke, propose, dry_run, node_classes, max_items, allow_relative=mode == "absolute")


def relink_missing_files(
    nuke: Any,
    search_folders: list[str],
    *,
    dry_run: bool = True,
    node_classes: list[str] | None = None,
    max_items: int = 256,
    max_search_files: int = 10000,
) -> dict[str, Any]:
    """Relink missing literal paths only when a bounded search has one match."""
    _bounded_int("max_search_files", max_search_files, 1, 100000)
    if not isinstance(search_folders, list) or not 1 <= len(search_folders) <= 32:
        raise FileReferenceValidationError("search_folders must contain 1 to 32 absolute folders")
    folders = [_literal_path("search folder", folder, absolute=True) for folder in search_folders]
    if any(not os.path.isdir(folder) for folder in folders):
        raise FileReferenceValidationError("every search folder must be an existing directory")
    files: set[str] = set()

    def walk_error(_error):
        raise FileReferenceRuntimeError("search folder could not be completely read")

    for folder in folders:
        for directory, subdirs, names in os.walk(folder, followlinks=False, onerror=walk_error):
            subdirs.sort()
            for name in sorted(names):
                candidate = os.path.join(directory, name).replace("\\", "/")
                files.add(candidate)
                if len(files) > max_search_files:
                    raise FileReferenceValidationError("search exceeds max_search_files; narrow the search folders")

    def propose(record):
        if record["exists"]:
            return None, "already_exists"
        basename = posixpath.basename(record["raw_value"].replace("\\", "/"))
        pattern = sequence_pattern(basename)
        candidates = sorted(
            {
                posixpath.join(posixpath.dirname(path), basename)
                for path in files
                if pattern.fullmatch(posixpath.basename(path)) and os.path.isfile(path)
            }
        )
        if len(candidates) != 1:
            record["candidates"] = candidates[:16]
            record["candidate_count"] = len(candidates)
            return None, "ambiguous" if candidates else "still_missing"
        return candidates[0], None

    return _change_paths(nuke, propose, dry_run, node_classes, max_items)


def _references(nuke, max_items, node_classes):
    _bounded_int("max_items", max_items, 1, _MAX_ITEMS)
    classes = _node_classes(node_classes)
    try:
        nodes = nuke.allNodes(group=nuke.root(), recurseGroups=True)
        nodes = sorted(nodes, key=lambda node: node.fullName())
        records = []
        count = 0
        for node in nodes:
            node_class = str(node.Class())
            if classes and node_class not in classes:
                continue
            for name, knob in sorted(node.knobs().items()):
                if knob.Class() != "File_Knob":
                    continue
                count += 1
                if count <= max_items:
                    records.append((_describe(node, node_class, name, knob), knob))
        return records, count
    except FileReferenceError:
        raise
    except Exception:
        raise FileReferenceRuntimeError("failed to enumerate Nuke file references") from None


def _describe(node, node_class, name, knob):
    record = {
        "node": str(node.fullName())[:256],
        "class": node_class[:128],
        "knob": str(name)[:128],
        "raw_value": None,
        "evaluated_path": None,
        "exists": None,
        "expression": None,
        "animated": None,
        "error": None,
    }
    try:
        raw = _raw_value(knob)
        record["raw_value"] = raw
        expression = knob.hasExpression()
        animated = knob.isAnimated()
        if not isinstance(expression, bool) or not isinstance(animated, bool):
            raise ValueError
        record["expression"] = expression or _expression(raw)
        record["animated"] = animated
        if not raw:
            record["evaluated_path"] = ""
            record["exists"] = False
            return record
    except Exception:
        record["error"] = "unreadable_file_knob"
    try:
        evaluated = knob.evaluate()
        if not isinstance(evaluated, str) or len(evaluated) > _MAX_PATH:
            raise ValueError
        record["evaluated_path"] = evaluated
        record["exists"] = path_exists(reference_path(record))
    except Exception:
        record["error"] = "evaluation_failed"
    return record


def _change_paths(nuke, propose, dry_run, node_classes, max_items, *, allow_relative=False):
    _boolean("dry_run", dry_run)
    records, count = _references(nuke, max_items, node_classes)
    if count > max_items:
        raise FileReferenceValidationError("references exceed max_items; narrow node_classes or increase the limit")
    changes = []
    for record, knob in records:
        raw = record["raw_value"]
        known_relative = isinstance(raw, str) and raw.startswith(_RELATIVE) and not _expression(raw[len(_RELATIVE) :])
        if record["error"]:
            reason = "unreadable"
        elif record["animated"]:
            reason = "animated"
        elif record["expression"] and not (allow_relative and known_relative):
            reason = "expression"
        elif not raw:
            reason = "empty"
        else:
            candidate, reason = propose(record)
            if candidate is not None and candidate != raw:
                literal = candidate[len(_RELATIVE) :] if candidate.startswith(_RELATIVE) else candidate
                _literal_path("replacement path", literal)
                record["new_value"] = candidate
                changes.append((record, knob))
            elif candidate == raw:
                reason = "unchanged"
        record["status"] = reason or ("would_change" if dry_run else "changed")
    if not dry_run:
        _apply(changes)
    return {
        "dry_run": dry_run,
        "references": [record for record, _knob in records],
        "reference_count": count,
        "change_count": len(changes),
        "truncated": False,
    }


def _apply(changes):
    touched = []
    try:
        for record, knob in changes:
            if _raw_value(knob) != record["raw_value"]:
                raise FileReferenceRuntimeError("file reference changed since inspection")
            touched.append((record, knob))
            knob.setValue(record["new_value"])
            if _raw_value(knob) != record["new_value"]:
                raise FileReferenceRuntimeError("file reference readback does not match request")
            record["readback_value"] = record["new_value"]
    except Exception:
        failed = False
        for record, knob in reversed(touched):
            try:
                knob.setValue(record["raw_value"])
                if _raw_value(knob) != record["raw_value"]:
                    failed = True
            except Exception:
                failed = True
        if failed:
            raise FileReferenceRuntimeError("file reference change failed and rollback could not be verified") from None
        raise FileReferenceRuntimeError("file reference change failed; all touched knobs were restored") from None


def _raw_value(knob):
    raw = knob.getText()
    if not isinstance(raw, str) or len(raw) > _MAX_PATH:
        raise FileReferenceRuntimeError("file knob value is not a bounded string")
    return raw


def sequence_pattern(basename: str) -> re.Pattern:
    """Match a literal or printf/hash basename, capturing the first frame as ``frame``."""
    parts = []
    previous = 0
    for index, match in enumerate(_SEQUENCE.finditer(basename)):
        parts.append(re.escape(basename[previous : match.start()]))
        token = match.group()
        width = len(token) if token.startswith("#") else int(match.group(1) or 0)
        # Width is a minimum for printf sequences; larger frame numbers remain valid.
        frame = r"(?:\d{%d,}|-\d{%d,})" % (width, max(1, width - 1)) if width else r"-?\d+"
        parts.append("(?P<frame>" + frame + ")" if index == 0 else frame)
        previous = match.end()
    parts.append(re.escape(basename[previous:]))
    return re.compile("".join(parts))


def _absolute(path):
    return path.startswith("/") or bool(re.match(r"^[A-Za-z]:/", path))


def _expression(path):
    return any(character in path for character in "[]$")


def _literal_path(label, value, *, absolute=False):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_PATH
        or any(ord(character) < 32 for character in value)
        or _expression(value)
    ):
        raise FileReferenceValidationError(f"{label} must be a bounded literal path without expressions")
    value = value.replace("\\", "/")
    if absolute and not _absolute(value):
        raise FileReferenceValidationError(f"{label} must be an absolute path")
    return value


def _node_classes(value):
    if value is None:
        return set()
    if not isinstance(value, list) or not 1 <= len(value) <= 64:
        raise FileReferenceValidationError("node_classes must contain 1 to 64 class names")
    if any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name) for name in value):
        raise FileReferenceValidationError("node_classes must contain literal Nuke class names")
    return set(value)


def _boolean(name, value):
    if not isinstance(value, bool):
        raise FileReferenceValidationError(f"{name} must be a boolean")


def _bounded_int(name, value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise FileReferenceValidationError(f"{name} must be an integer from {minimum} to {maximum}")
