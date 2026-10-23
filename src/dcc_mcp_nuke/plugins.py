"""Read-only Nuke plug-in inventory and bounded local asset discovery."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

_CLASS = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")
_CLASS_FILE_EXTENSIONS = {".dll", ".so", ".dylib", ".gizmo", ".gznc", ".py", ".tcl", ".ofx"}
_KINDS = {
    ".dll": "native",
    ".so": "native",
    ".dylib": "native",
    ".ofx": "ofx",
    ".gizmo": "gizmo",
    ".gznc": "gizmo_noncommercial",
    ".py": "python",
    ".tcl": "tcl",
}


def plugin_class_names(inventory: Iterable[str]) -> set[str]:
    """Keep exact registered names and known plug-in filename-stem candidates."""
    names = set()
    for entry in inventory:
        name = str(entry)
        names.add(name)
        path = Path(name.replace("\\", "/"))
        if path.suffix.lower() in _CLASS_FILE_EXTENSIONS:
            names.add(path.stem)
    return names


def inspect_plugin_environment(
    nuke: Any,
    *,
    required_node_classes: list[str] | None = None,
    task_roots: list[str] | None = None,
    name_filter: str = "",
    max_items: int = 128,
    max_scan_entries: int = 4096,
) -> dict[str, Any]:
    """Inspect paths/files/classes without adding paths, loading code or creating nodes."""
    _bounded_int("max_items", max_items, 1, 1024)
    _bounded_int("max_scan_entries", max_scan_entries, 1, 100000)
    if not isinstance(name_filter, str) or len(name_filter) > 128 or any(ord(char) < 32 for char in name_filter):
        raise ValueError("name_filter must be a literal substring of at most 128 characters")
    requested = [] if required_node_classes is None else required_node_classes
    if (
        not isinstance(requested, list)
        or len(requested) > 256
        or any(not isinstance(name, str) or not _CLASS.fullmatch(name) for name in requested)
    ):
        raise ValueError("required_node_classes must contain at most 256 literal class names")
    roots = [] if task_roots is None else task_roots
    if not isinstance(roots, list) or len(roots) > 16:
        raise ValueError("task_roots must contain at most 16 absolute directories")
    roots = list(dict.fromkeys(_task_root(value) for value in roots))
    issues = []
    probes = {}

    def probe(name, callback):
        try:
            raw = callback()
            if not isinstance(raw, (list, tuple)) or any(
                not isinstance(entry, str) or len(entry) > 8192 or any(ord(char) < 32 for char in entry)
                for entry in raw[:max_scan_entries]
            ):
                raise ValueError
            truncated = len(raw) > max_scan_entries
            probes[name] = {"available": True, "count": len(raw), "truncated": truncated}
            return list(raw[:max_scan_entries])
        except Exception:
            probes[name] = {"available": False, "count": 0, "truncated": False}
            issues.append({"code": name + "_unavailable"})
            return []

    paths = probe("plugin_paths", lambda: nuke.pluginPath())
    available = probe("available_plugins", lambda: nuke.plugins(nuke.ALL))
    loaded = probe("loaded_plugins", lambda: nuke.plugins())
    registered_entries = probe("registered_classes", lambda: nuke.plugins(nuke.REGISTERED))
    registered = sorted({entry for entry in registered_entries if _kind(entry) is None and _CLASS.fullmatch(entry)})
    loaded_keys = {_path_key(entry) for entry in loaded if _kind(entry) is not None}
    active_keys = {_path_key(path) for path in paths}
    assets, root_reports, scan_incomplete = _scan_roots(roots, active_keys, loaded_keys, max_scan_entries)
    if scan_incomplete:
        issues.append({"code": "task_root_scan_incomplete"})
    catalog = [_asset(entry, loaded_keys, "host_inventory") for entry in sorted(set(available)) if _kind(entry)]
    loaded_catalog = [_asset(entry, loaded_keys, "loaded_inventory") for entry in sorted(set(loaded)) if _kind(entry)]
    candidate_names = plugin_class_names([*available, *(asset["path"] for asset in assets)])
    incomplete = scan_incomplete or any(not state["available"] or state["truncated"] for state in probes.values())
    requirements = []
    for name in sorted(set(requested)):
        if name in registered:
            status = "registered"
        elif name in candidate_names:
            status = "discoverable_candidate"
        else:
            status = "unverified" if incomplete else "not_registered"
        requirements.append({"node_class": name, "status": status, "runtime_validation": "not_run"})
    needle = name_filter.casefold()

    def selected(items, key):
        matching = [item for item in items if needle in str(key(item)).casefold()]
        return matching[:max_items], len(matching), len(matching) > max_items

    plugin_paths = [{"path": path, "exists": Path(path).is_dir(), "order": index} for index, path in enumerate(paths)]
    result = {
        "host": {
            "nuke_version": str(getattr(nuke, "NUKE_VERSION_STRING", "unknown"))[:128],
            "python_version": sys.version.split()[0],
            "platform": sys.platform,
        },
        "probes": probes,
        "task_roots": root_reports,
        "task_scan_incomplete": scan_incomplete,
        "requirements": requirements,
        "issues": issues,
        "scope": "read-only metadata; no path activation, loading, node creation, ABI or license validation",
        "plugin_paths": plugin_paths[:max_items],
        "plugin_paths_count": probes["plugin_paths"]["count"],
        "plugin_paths_truncated": len(plugin_paths) > max_items or probes["plugin_paths"]["truncated"],
    }
    for name, items, key in (
        ("available_plugins", catalog, lambda item: item["path"]),
        ("loaded_plugins", loaded_catalog, lambda item: item["path"]),
        ("registered_node_classes", registered, lambda item: item),
        ("task_assets", assets, lambda item: item["path"]),
    ):
        values, count, truncated = selected(items, key)
        result[name] = values
        result[name + "_count"] = count
        result[name + "_truncated"] = truncated
    return result


def _scan_roots(roots, active_keys, loaded_keys, limit):
    assets = []
    reports = []
    count = 0
    incomplete = False
    for root in roots:
        report = {
            "path": str(root),
            "exists": root.is_dir(),
            "active_search_path": _path_key(str(root)) in active_keys,
            "entries_scanned": 0,
            "incomplete": False,
        }
        reports.append(report)
        if not report["exists"]:
            continue
        stack = [(root, 0)]
        while stack:
            directory, depth = stack.pop()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if count >= limit:
                            report["incomplete"] = incomplete = True
                            stack.clear()
                            break
                        count += 1
                        report["entries_scanned"] += 1
                        path = Path(entry.path)
                        resolved = path.resolve()
                        if entry.is_symlink() or root not in resolved.parents:
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if path.name.lower().endswith(".ofx.bundle"):
                                assets.append(_asset(str(path), loaded_keys, "task_root"))
                            elif depth < 4:
                                stack.append((path, depth + 1))
                            else:
                                report["incomplete"] = incomplete = True
                        elif entry.is_file(follow_symlinks=False) and _kind(str(path)):
                            assets.append(_asset(str(path), loaded_keys, "task_root"))
            except OSError:
                report["incomplete"] = incomplete = True
    return sorted(assets, key=lambda item: item["path"]), reports, incomplete


def _asset(path, loaded_keys, source):
    candidate = Path(path)
    try:
        metadata = candidate.stat()
        exists = True
        size = metadata.st_size if candidate.is_file() else None
    except OSError:
        exists = False
        size = None
    return {
        "path": path,
        "kind": _kind(path),
        "exists": exists,
        "size_bytes": size,
        "loaded": _path_key(path) in loaded_keys,
        "source": source,
    }


def _kind(path):
    normalized = path.replace("\\", "/").lower()
    if normalized.endswith(".ofx.bundle"):
        return "ofx_bundle"
    return _KINDS.get(Path(normalized).suffix)


def _path_key(path):
    return os.path.normcase(os.path.abspath(path))


def _task_root(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 8192
        or any(ord(char) < 32 for char in value)
        or not Path(value).is_absolute()
    ):
        raise ValueError("each task root must be a bounded absolute directory path")
    return Path(value).resolve()


def _bounded_int(name, value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
