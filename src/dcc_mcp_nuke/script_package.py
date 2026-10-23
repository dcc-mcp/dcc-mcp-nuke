"""Collect the active Nuke script without saving or retaining changes to it."""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from dcc_mcp_core.cancellation import check_cancelled

from dcc_mcp_nuke.file_references import (
    FileReferenceError,
    FileReferenceRuntimeError,
    FileReferenceValidationError,
    _boolean,
    _bounded_int,
    _describe,
    _expression,
    _literal_path,
    _references,
    reference_path,
    sequence_files,
    sequence_pattern,
)
from dcc_mcp_nuke.gizmos import _publish_file

_RELATIVE = "[file dirname [value root.name]]/"
_OUTPUT_CLASSES = {"Write", "DeepWrite", "WriteGeo", "WriteGeo2"}
_FONT = re.compile(r"^(.*?\s:\s.*?\s:\s)(.*?)(\s:\s[0-9]+\s*\}?)$")
_SCRIPT_EXTENSIONS = {".gizmo", ".gznc", ".py", ".tcl"}
_MAX_MANIFEST_BYTES = 50000000


def package_script(
    nuke: Any,
    output_path: str,
    *,
    dry_run: bool = True,
    frame_mode: str = "all",
    first_frame: int | None = None,
    last_frame: int | None = None,
    max_references: int = 1024,
    max_files: int = 10000,
    max_total_bytes: int = 1000000000000,
    max_report_items: int = 256,
) -> dict[str, Any]:
    """Preview or publish a directory/zip with media, a copied script and manifest."""
    _boolean("dry_run", dry_run)
    _bounded_int("max_files", max_files, 1, 100000)
    _bounded_int("max_total_bytes", max_total_bytes, 1, 1000000000000000)
    _bounded_int("max_report_items", max_report_items, 1, 1024)
    if frame_mode not in ("all", "used"):
        raise FileReferenceValidationError("frame_mode must be all or used")
    target = Path(_literal_path("output_path", output_path, absolute=True)).resolve()
    if target.exists():
        raise FileReferenceValidationError("output_path already exists; packages never overwrite targets")
    if not target.parent.is_dir():
        raise FileReferenceValidationError("output_path parent must be an existing directory")
    root = nuke.root()
    first = root.firstFrame() if first_frame is None else first_frame
    last = root.lastFrame() if last_frame is None else last_frame
    _bounded_int("first_frame", first, -1000000, 1000000)
    _bounded_int("last_frame", last, -1000000, 1000000)
    if last < first or last - first > 100000:
        raise FileReferenceValidationError("frame range must be ordered and span at most 100001 frames")
    records, count = _references(nuke, max_references, None)
    if count > max_references:
        raise FileReferenceValidationError("references exceed max_references")
    nodes = {node.fullName(): node for node in nuke.allNodes(group=root, recurseGroups=True)}
    if len(nodes) > max_references:
        raise FileReferenceValidationError("node count exceeds max_references")
    for name, knob in sorted(root.knobs().items()):
        if name != "name" and knob.Class() == "File_Knob":
            records.append((_describe(root, "Root", name, knob), knob))
    if len(records) > max_references:
        raise FileReferenceValidationError("references including Root exceed max_references")
    files: dict[str, dict[str, Any]] = {}
    destinations = {}
    total_bytes = 0
    rewrites = []
    missing = []
    unrewritten = []
    outputs = []

    def add_file(source, kind):
        nonlocal total_bytes
        source = Path(source).resolve()
        key = str(source)
        if key not in files:
            size = source.stat().st_size
            digest = hashlib.sha256(str(source.parent).encode("utf-8")).hexdigest()[:16]
            relative = _safe_relative(f"assets/{digest}/{source.name}")
            if relative in destinations and destinations[relative] != key:
                raise FileReferenceRuntimeError("dependency destination collision")
            destinations[relative] = key
            files[key] = {"source": key, "relative_path": relative, "size_bytes": size, "kind": kind}
            total_bytes += size
            if len(files) > max_files or total_bytes > max_total_bytes:
                raise FileReferenceValidationError("package exceeds max_files or max_total_bytes")
        return files[key]["relative_path"]

    used = _used_files(nodes, first, last) if frame_mode == "used" else {}
    for record, knob in records:
        check_cancelled()
        identity = {key: record[key] for key in ("node", "class", "knob")}
        raw = record["raw_value"]
        if not raw:
            if record["error"]:
                unrewritten.append(dict(identity, reason="unreadable"))
            continue
        if record["class"] in _OUTPUT_CLASSES:
            if record["expression"] or record["animated"] or record["error"]:
                unrewritten.append(dict(identity, reason="dynamic_output"))
                continue
            relative = f"renders/{_safe_name(record['node'])}/{posixpath.basename(raw.replace(chr(92), '/'))}"
            outputs.append(posixpath.dirname(relative))
            rewrites.append((knob, _RELATIVE + relative, False))
            continue
        source_path = reference_path(record)
        if not source_path or record["error"]:
            unrewritten.append(dict(identity, reason="unreadable"))
            continue
        source_path = _resolve_source(nuke, source_path)
        sources = sequence_files(source_path, max_files=max_files)
        if frame_mode == "used" and "frame" in sequence_pattern(Path(source_path).name).groupindex:
            source_node = record["node"]
            needed = used.get(source_node)
            if needed is None:
                raise FileReferenceRuntimeError("used-frame dependencies could not be proven for a sequence")
            sources = [source for source in sources if os.path.normcase(os.path.abspath(source)) in needed]
            pattern = sequence_pattern(Path(source_path).name)
            for needed_path in sorted(needed):
                if pattern.fullmatch(Path(needed_path).name) and not Path(needed_path).is_file():
                    missing.append(dict(identity, path=needed_path))
        if not sources:
            missing.append(dict(identity, path=source_path))
            continue
        relative = None
        for source in sources:
            copied = add_file(source, "file")
            relative = posixpath.join(posixpath.dirname(copied), Path(source_path).name)
        if Path(source_path).suffix.lower() == ".ocio":
            unrewritten.append(dict(identity, reason="ocio_dependent_luts_not_resolved"))
        if record["expression"] or record["animated"]:
            unrewritten.append(dict(identity, reason="expression" if record["expression"] else "animated"))
        else:
            rewrites.append((knob, _RELATIVE + relative, False))

    plugins, runtime = _plugin_dependencies(nuke, nodes, add_file, max_files)
    _font_dependencies(nuke, nodes, add_file, rewrites, missing, unrewritten, plugins)
    manifest = {
        "schema_version": 1,
        "script": "script.nk",
        "original_script": str(root.name()),
        "frame_mode": frame_mode,
        "frame_range": [first, last],
        "files": sorted(files.values(), key=lambda item: item["relative_path"]),
        "missing": missing,
        "unrewritten": unrewritten,
        "runtime_dependencies": runtime,
        "plugin_directories": sorted(set(plugins)),
        "rewrite_count": len(rewrites),
        "total_size_bytes": sum(item["size_bytes"] for item in files.values()),
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2)
    if len(manifest_text.encode("utf-8")) > _MAX_MANIFEST_BYTES:
        raise FileReferenceValidationError("package manifest exceeds 50 MB; reduce max_files")
    result = _report(manifest, max_report_items)
    result.update(dry_run=dry_run, output_path=str(target), files_copied=0)
    if dry_run:
        return result
    with tempfile.TemporaryDirectory(prefix=".dcc-mcp-collect-", dir=str(target.parent)) as temporary:
        stage = Path(temporary) / "package"
        stage.mkdir()
        for item in manifest["files"]:
            check_cancelled()
            destination = stage / item["relative_path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            before = Path(item["source"]).stat()
            with Path(item["source"]).open("rb") as source, destination.open("xb") as copied:
                _copy_stream(source, copied)
            shutil.copystat(item["source"], destination)
            after = Path(item["source"]).stat()
            if destination.stat().st_size != item["size_bytes"] or (before.st_size, before.st_mtime_ns) != (
                after.st_size,
                after.st_mtime_ns,
            ):
                raise FileReferenceRuntimeError("source dependency changed during copy or copied size does not match")
        for relative in outputs:
            (stage / relative).mkdir(parents=True, exist_ok=True)
        _save_snapshot(nuke, stage / "script.nk", rewrites)
        check_cancelled()
        (stage / "manifest.json").write_text(manifest_text, encoding="utf-8")
        (stage / "README.txt").write_text(
            "Open script.nk in a compatible Nuke installation.\n"
            "Review manifest.json for missing files, unrewritten paths, OCIO LUTs and runtime plug-ins.\n"
            "For collected plug-in scripts and fonts, add plugin_directories to NUKE_PATH before starting Nuke.\n",
            encoding="utf-8",
        )
        inspect_package(str(stage), max_report_items=max_report_items)
        if target.suffix.lower() == ".zip":
            archive = Path(temporary) / "package.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as output:
                for path in sorted(stage.rglob("*")):
                    check_cancelled()
                    relative = path.relative_to(stage).as_posix()
                    if path.is_dir():
                        output.write(path, relative)
                    else:
                        with path.open("rb") as source, output.open(relative, "w", force_zip64=True) as copied:
                            _copy_stream(source, copied)
            check_cancelled()
            _publish_file(archive, target, overwrite=False)
        else:
            _publish_directory(stage, target)
    inspected = inspect_package(str(target), max_report_items=max_report_items)
    result.update(files_copied=len(files), verified=inspected["verified"])
    return result


def inspect_package(output_path: str, *, max_report_items: int = 256) -> dict[str, Any]:
    """Read a package receipt and verify every listed dependency's stored size."""
    _bounded_int("max_report_items", max_report_items, 1, 1024)
    target = Path(_literal_path("output_path", output_path, absolute=True)).resolve()
    archive = None
    try:
        if target.is_file() and target.suffix.lower() == ".zip":
            archive = zipfile.ZipFile(target)
        if archive:
            if len(archive.infolist()) > 200000:
                raise FileReferenceValidationError("package archive exceeds entry limit")
            for info in archive.infolist():
                _safe_relative(info.filename)
            if archive.getinfo("manifest.json").file_size > _MAX_MANIFEST_BYTES:
                raise FileReferenceValidationError("package manifest exceeds 50 MB")
            manifest = json.loads(archive.read("manifest.json"))
        else:
            receipt = target / "manifest.json"
            if receipt.stat().st_size > _MAX_MANIFEST_BYTES:
                raise FileReferenceValidationError("package manifest exceeds 50 MB")
            manifest = json.loads(receipt.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise FileReferenceValidationError("unsupported package manifest")
        files = manifest.get("files")
        if not isinstance(files, list) or len(files) > 100000:
            raise FileReferenceValidationError("package files list must contain at most 100000 entries")
        for item in files + [{"relative_path": manifest["script"], "size_bytes": None}]:
            relative = _safe_relative(item["relative_path"])
            expected = item["size_bytes"]
            if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int) or expected < 0):
                raise FileReferenceValidationError("package file sizes must be non-negative integers")
            size = archive.getinfo(relative).file_size if archive else _contained_file(target, relative).stat().st_size
            if expected is not None and size != expected:
                raise FileReferenceRuntimeError("packaged file size does not match its receipt")
        result = _report(manifest, max_report_items)
        result.update(output_path=str(target), verified=True)
        return result
    except FileReferenceError:
        raise
    except Exception:
        raise FileReferenceRuntimeError("failed to inspect package receipt and contents") from None
    finally:
        if archive:
            archive.close()


def _save_snapshot(nuke, path, rewrites):
    root = nuke.root()
    original_name = root.name()
    modified = root.modified()
    if not isinstance(modified, bool) or not callable(getattr(root, "setModified", None)):
        raise FileReferenceRuntimeError("Root modified state cannot be restored")
    snapshots = [(knob, knob.toScript(), value, serialized) for knob, value, serialized in rewrites]
    touched = []
    failure = None
    nuke.Undo.disable()
    try:
        for knob, before, value, serialized in snapshots:
            touched.append((knob, before))
            if serialized:
                knob.fromScript(value)
                matches = knob.toScript() == value
            else:
                knob.setValue(value)
                matches = knob.getText() == value
            if not matches:
                raise FileReferenceRuntimeError("copied script path readback does not match request")
        nuke.scriptSaveToTemp(str(path))
        if not path.is_file() or not path.stat().st_size:
            raise FileReferenceRuntimeError("Nuke did not serialize the copied script")
    except Exception:
        failure = "failed to serialize the copied script"
    finally:
        for knob, before in reversed(touched):
            try:
                knob.fromScript(before)
                if knob.toScript() != before:
                    failure = "original knob rollback could not be verified"
            except Exception:
                failure = "original knob rollback could not be verified"
        try:
            if root.name() != original_name:
                root.setName(original_name)
            root.setModified(modified)
            if root.name() != original_name or root.modified() != modified:
                failure = "original Root state rollback could not be verified"
        except Exception:
            failure = "original Root state rollback could not be verified"
        finally:
            nuke.Undo.enable()
    if failure:
        raise FileReferenceRuntimeError(failure)


def _used_files(nodes, first, last):
    outputs = [node for node in nodes.values() if node.Class() in _OUTPUT_CLASSES]
    queries = outputs or list(nodes.values())
    used = {}
    try:
        for node in queries:
            for dependency, paths in node.fileDependencies(first, last):
                if not isinstance(paths, (list, tuple)) or len(paths) > 100000:
                    raise ValueError
                key = dependency.fullName()
                used.setdefault(key, set()).update(os.path.normcase(os.path.abspath(path)) for path in paths)
        return used
    except Exception:
        raise FileReferenceRuntimeError("Nuke used-frame dependency query failed") from None


def _plugin_dependencies(nuke, nodes, add_file, max_files):
    directories = []
    runtime = []
    try:
        available = nuke.plugins(nuke.ALL | nuke.REGISTERED)
        if not isinstance(available, list) or len(available) > max_files:
            raise ValueError
    except Exception:
        available = []
    for node_class in sorted({str(node.Class()) for node in nodes.values()}):
        candidates = {Path(path) for path in available if Path(path).stem == node_class and Path(path).is_file()}
        for node in nodes.values():
            if node.Class() == node_class and callable(getattr(node, "filename", None)):
                try:
                    candidate = _find_plugin_file(nuke, node.filename())
                except Exception:
                    candidate = None
                if candidate:
                    candidates.add(candidate)
        source_scripts = [path for path in candidates if path.suffix.lower() in _SCRIPT_EXTENSIONS]
        if len(source_scripts) == 1:
            relative = add_file(source_scripts[0], "plugin_script")
            directories.append(posixpath.dirname(relative))
            runtime.append(
                {
                    "class": node_class,
                    "kind": "script",
                    "included": relative,
                    "reason": "transitive_imports_not_resolved",
                }
            )
        else:
            runtime.append(
                {
                    "class": node_class,
                    "kind": "compiled_or_host",
                    "included": None,
                    "reason": "requires_compatible_host_or_plugin",
                    "candidates": [str(path) for path in sorted(candidates)[:16]],
                }
            )
    return directories, runtime


def _font_dependencies(nuke, nodes, add_file, rewrites, missing, unrewritten, plugin_directories):
    for name, node in nodes.items():
        for knob_name, knob in node.knobs().items():
            if knob.Class() not in ("FreeType_Knob", "Font_Knob"):
                continue
            identity = {"node": name, "class": str(node.Class()), "knob": knob_name}
            serialized = knob.toScript()
            match = _FONT.fullmatch(serialized) if isinstance(serialized, str) and len(serialized) <= 8192 else None
            if not match or knob.isAnimated() is not False or knob.hasExpression() is not False:
                unrewritten.append(dict(identity, reason="unsupported_font_serialization"))
                continue
            source = _find_plugin_file(nuke, match.group(2).strip())
            if source is None:
                missing.append(dict(identity, path=match.group(2).strip()))
                continue
            relative = add_file(source, "font")
            plugin_directories.append(posixpath.dirname(relative))
            # FreeType filenames use the font/plugin search path rather than File_Knob Tcl evaluation.
            rewritten = match.group(1) + source.name + match.group(3)
            if rewritten != serialized:
                rewrites.append((knob, rewritten, True))


def _find_plugin_file(nuke, filename):
    if not isinstance(filename, str) or not filename or len(filename) > 8192 or _expression(filename):
        return None
    candidate = Path(filename)
    if candidate.is_absolute() and candidate.is_file():
        return candidate.resolve()
    try:
        folders = nuke.pluginPath()
    except Exception:
        return None
    if not isinstance(folders, list) or len(folders) > 1024:
        return None
    for folder in folders:
        for child in ("", "fonts"):
            candidate = Path(folder) / child / filename
            if candidate.is_file():
                return candidate.resolve()
    return None


def _resolve_source(nuke, path):
    if Path(path).is_absolute():
        return path
    try:
        folder = nuke.root()["project_directory"].evaluate()
    except Exception:
        folder = ""
    if not folder:
        folder = str(Path(nuke.root().name()).parent)
    return str(Path(folder) / path)


def _publish_directory(stage, target):
    check_cancelled()
    target.mkdir(exist_ok=False)
    owned = target.stat()
    try:
        # shortcut: directories use a manifest receipt, add an OS-specific no-replace rename for atomic visibility.
        for child in sorted(stage.iterdir(), key=lambda path: path.name == "manifest.json"):
            check_cancelled()
            child.rename(target / child.name)
    except Exception:
        current = target.stat()
        if (owned.st_dev, owned.st_ino) == (current.st_dev, current.st_ino):
            shutil.rmtree(target)
        raise


def _copy_stream(source, destination):
    for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
        check_cancelled()
        destination.write(chunk)


def _safe_relative(value):
    if not isinstance(value, str) or not value or len(value) > 8192:
        raise FileReferenceValidationError("package paths must be bounded relative names")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
        raise FileReferenceValidationError("package path escapes the package root")
    return value


def _contained_file(root, relative):
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        raise FileReferenceValidationError("package path escapes the package root or is not a file")
    return path


def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:128]


def _report(manifest, limit):
    result = {
        key: manifest[key]
        for key in (
            "schema_version",
            "script",
            "frame_mode",
            "frame_range",
            "rewrite_count",
            "total_size_bytes",
            "plugin_directories",
        )
    }
    for key in ("files", "missing", "unrewritten", "runtime_dependencies"):
        items = manifest[key]
        result[key] = items[:limit]
        result[key + "_count"] = len(items)
        result[key + "_truncated"] = len(items) > limit
    return result
