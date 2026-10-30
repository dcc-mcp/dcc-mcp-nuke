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
    _expression,
    _literal_path,
    reference_path,
    sequence_files,
    sequence_pattern,
)
from dcc_mcp_nuke.gizmos import _publish_file
from dcc_mcp_nuke.script_package_scene import (
    OUTPUT_CLASSES as _OUTPUT_CLASSES,
)
from dcc_mcp_nuke.script_package_scene import (
    PackageScriptError,
    capture_scene,
    capture_used_files,
    describe_scene_change,
    on_host_thread,
    scene_signature,
)
from dcc_mcp_nuke.script_package_scene import (
    serialization as _serialization,
)

_RELATIVE = "[file dirname [value root.name]]/"
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
    check_cancelled()
    target = Path(_literal_path("output_path", output_path, absolute=True)).resolve()
    if target.exists():
        raise FileReferenceValidationError("output_path already exists; packages never overwrite targets")
    if not target.parent.is_dir():
        raise FileReferenceValidationError("output_path parent must be an existing directory")
    cwd = os.getcwd()
    scene = on_host_thread(
        nuke,
        lambda: capture_scene(
            nuke,
            frame_mode=frame_mode,
            first_frame=first_frame,
            last_frame=last_frame,
            max_references=max_references,
            max_files=max_files,
            require_signature=not dry_run,
        ),
    )
    if frame_mode == "used":
        scene = capture_used_files(nuke, scene, max_files, max_references)
    first, last = scene.first, scene.last
    files: dict[str, dict[str, Any]] = {}
    destinations = {}
    total_bytes = 0
    rewrites = []
    missing = []
    unrewritten = []
    outputs = []
    unused = []

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

    # Keep the host-reported, original-case paths for real file I/O. Case
    # folding belongs only to identity comparison (see _used_reference_paths),
    # never to the paths we open, stat or copy.
    used = {node: {os.path.abspath(os.path.join(cwd, path)) for path in paths} for node, paths in scene.used.items()}
    for record in scene.records:
        check_cancelled()
        identity = {key: record[key] for key in ("node", "class", "knob")}
        raw = record["raw_value"]
        if (
            frame_mode == "used"
            and record["class"] not in {*_OUTPUT_CLASSES, "Root"}
            and (record["error"] or (not raw and (record["expression"] or record["animated"])))
        ):
            raise PackageScriptError(
                "used-frame dependencies could not be proven for an unreadable reference",
                "reference_unreadable",
                **identity,
                path=(raw or record["evaluated_path"] or "")[:8192],
                frame_range=[first, last],
            )
        if not raw:
            if record["error"]:
                unrewritten.append(dict(identity, reason="unreadable"))
            continue
        if record["class"] == "Root" and record["knob"] == "OCIOConfigPath":
            # Nuke derives this read-only value even when enabled()/READ_ONLY report it writable.
            unrewritten.append(dict(identity, reason="host_managed_ocio_config"))
            continue
        multiple_contexts = record["multiple_contexts"]
        if record["class"] in _OUTPUT_CLASSES:
            if multiple_contexts or record["expression"] or record["animated"] or record["error"]:
                unrewritten.append(
                    dict(identity, reason="multiple_context_path" if multiple_contexts else "dynamic_output")
                )
                continue
            relative = f"renders/{_safe_name(record['node'])}/{posixpath.basename(raw.replace(chr(92), '/'))}"
            outputs.append(posixpath.dirname(relative))
            rewrites.append((identity, _RELATIVE + relative, False))
            continue
        source_path = reference_path(record)
        if not source_path or record["error"]:
            unrewritten.append(dict(identity, reason="unreadable"))
            continue
        if (
            frame_mode == "used"
            and scene.used_complete
            and scene.has_outputs
            and record["class"] == "Read"
            and record["node"] not in used
            and not multiple_contexts
            and not record["expression"]
            and not record["animated"]
        ):
            unused.append(dict(identity, path=source_path, reason="not_reported_by_outputs"))
            continue
        source_path = _resolve_source(scene, source_path, cwd)
        if frame_mode == "used" and "frame" in sequence_pattern(Path(source_path).name).groupindex:
            needed = _used_reference_paths(scene, record, source_path, used.get(record["node"]))
            sources = []
            for needed_path in needed:
                check_cancelled()
                if Path(needed_path).is_file():
                    sources.append(needed_path)
                else:
                    missing.append(dict(identity, path=needed_path))
        else:
            sources = sequence_files(source_path, max_files=max_files)
        if not sources:
            missing.append(dict(identity, path=source_path))
            continue
        relative = None
        for source in sources:
            copied = add_file(source, "file")
            relative = posixpath.join(posixpath.dirname(copied), Path(source_path).name)
        if Path(source_path).suffix.lower() == ".ocio":
            unrewritten.append(dict(identity, reason="ocio_dependent_luts_not_resolved"))
        if multiple_contexts:
            unrewritten.append(dict(identity, reason="multiple_context_path"))
        elif record["expression"] or record["animated"]:
            unrewritten.append(dict(identity, reason="expression" if record["expression"] else "animated"))
        else:
            rewrites.append((identity, _RELATIVE + relative, False))

    plugins, runtime = _plugin_dependencies(scene, add_file, cwd)
    _font_dependencies(scene, add_file, rewrites, missing, unrewritten, plugins, cwd)
    manifest = {
        "schema_version": 1,
        "script": "script.nk",
        "original_script": scene.original_script,
        "frame_mode": frame_mode,
        "frame_range": [first, last],
        "files": sorted(files.values(), key=lambda item: item["relative_path"]),
        "missing": missing,
        "unused_references": unused,
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
        _serialize_package_scene(nuke, scene, stage / "script.nk", rewrites, max_references)
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


def _used_reference_paths(scene, record, source_path, needed):
    reason = None
    if needed is None:
        reason = "dependency_not_reported"
    elif not needed:
        reason = "dependency_paths_empty"
    else:
        # Compare identity with ONE platform-aware normalization applied to BOTH
        # sides: the sequence pattern (built from the knob basename) and each
        # candidate basename (from the native dependency query). os.path.normcase
        # folds case on Windows' case-insensitive filesystem and is a no-op on
        # case-sensitive POSIX, so case is never ignored where it is significant.
        # The original, unmodified paths in `needed` are returned for real I/O.
        pattern = sequence_pattern(os.path.normcase(Path(source_path).name))
        directory = os.path.normcase(os.path.abspath(Path(source_path).parent))
        needed = sorted(
            path
            for path in needed
            if pattern.fullmatch(os.path.normcase(Path(path).name))
            and os.path.normcase(os.path.abspath(Path(path).parent)) == directory
        )
        if not needed:
            reason = "dependency_path_mismatch"
    if reason:
        raise PackageScriptError(
            "used-frame dependencies could not be proven for a sequence",
            reason,
            **{key: record[key] for key in ("node", "class", "knob")},
            path=source_path[:8192],
            frame_range=[scene.first, scene.last],
        )
    return needed


def _serialize_package_scene(nuke, scene, path, rewrites, max_references):
    # Native serialization stays local; slow network writes and validation run
    # on the worker after the live graph has been restored.
    with tempfile.TemporaryDirectory(prefix="dcc-mcp-collect-script-") as temporary:
        local = Path(temporary) / "script.nk"

        def serialize():
            current = scene_signature(nuke, max_references)
            if current != scene.signature:
                raise PackageScriptError(
                    "Nuke scene changed since collection snapshot",
                    "scene_changed_since_snapshot",
                    **describe_scene_change(scene.signature, current),
                )
            root = nuke.root()
            nodes = {node.fullName(): node for node in nuke.allNodes(group=root, recurseGroups=True)}
            bound = []
            for identity, value, serialized in rewrites:
                node = root if identity["class"] == "Root" else nodes[identity["node"]]
                bound.append((node.knobs()[identity["knob"]], value, serialized))
            _save_snapshot(nuke, local, bound, verify_file=False)

        on_host_thread(nuke, serialize)
        if not local.is_file() or not local.stat().st_size:
            raise FileReferenceRuntimeError("Nuke did not serialize the copied script")
        shutil.copyfile(local, path)


def _save_snapshot(nuke, path, rewrites, *, verify_file=True):
    root = nuke.root()
    original_name = root.name()
    modified = root.modified()
    if not isinstance(modified, bool) or not callable(getattr(root, "setModified", None)):
        raise FileReferenceRuntimeError("Root modified state cannot be restored")
    snapshots = []
    for knob, value, serialized in rewrites:
        before = _serialization(knob, None)
        if before != _serialization(knob, nuke.OutputContext()):
            raise FileReferenceRuntimeError("multiple-context knobs cannot be temporarily rewritten")
        snapshots.append((knob, before, value, serialized))
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
        if verify_file and (not path.is_file() or not path.stat().st_size):
            raise FileReferenceRuntimeError("Nuke did not serialize the copied script")
    except Exception:
        failure = "failed to serialize the copied script"
    finally:
        for knob, before in reversed(touched):
            try:
                knob.fromScript(before)
                if _serialization(knob, None) != before:
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


def _plugin_dependencies(scene, add_file, cwd):
    directories = []
    runtime = []
    for node_class in sorted({node["class"] for node in scene.node_plugins}):
        candidates = {
            Path(cwd, path) for path in scene.plugins if Path(path).stem == node_class and Path(cwd, path).is_file()
        }
        for node in scene.node_plugins:
            if node["class"] == node_class:
                candidate = _find_plugin_file(scene, node["filename"], cwd)
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


def _font_dependencies(scene, add_file, rewrites, missing, unrewritten, plugin_directories, cwd):
    for font in scene.fonts:
        identity = {key: font[key] for key in ("node", "class", "knob")}
        serialized = font["serialized"]
        match = _FONT.fullmatch(serialized) if isinstance(serialized, str) and len(serialized) <= 8192 else None
        if not match or not font["static"]:
            unrewritten.append(dict(identity, reason="unsupported_font_serialization"))
            continue
        source = _find_plugin_file(scene, match.group(2).strip(), cwd)
        if source is None:
            missing.append(dict(identity, path=match.group(2).strip()))
            continue
        relative = add_file(source, "font")
        plugin_directories.append(posixpath.dirname(relative))
        if font["full_serialized"] != serialized:
            unrewritten.append(dict(identity, reason="multiple_context_font"))
            continue
        # FreeType filenames use the font/plugin search path rather than File_Knob Tcl evaluation.
        rewritten = match.group(1) + source.name + match.group(3)
        if rewritten != serialized:
            rewrites.append((identity, rewritten, True))


def _find_plugin_file(scene, filename, cwd):
    if not isinstance(filename, str) or not filename or len(filename) > 8192 or _expression(filename):
        return None
    candidate = Path(filename)
    if candidate.is_absolute() and candidate.is_file():
        return candidate.resolve()
    for folder in scene.plugin_paths:
        for child in ("", "fonts"):
            candidate = Path(cwd, folder) / child / filename
            if candidate.is_file():
                return candidate.resolve()
    return None


def _resolve_source(scene, path, cwd):
    if Path(path).is_absolute():
        return path
    folder = scene.project_directory
    if not folder:
        folder = str(Path(scene.original_script).parent)
    return str(Path(cwd, folder, path))


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
    for key in ("files", "missing", "unrewritten", "runtime_dependencies", "unused_references"):
        items = manifest.get(key, []) if key == "unused_references" else manifest[key]
        result[key] = items[:limit]
        result[key + "_count"] = len(items)
        result[key + "_truncated"] = len(items) > limit
    return result
