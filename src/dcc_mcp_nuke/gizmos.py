from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

PLUGIN_ROOT_ENV = "DCC_MCP_NUKE_PLUGIN_ROOT"
SCHEMA_VERSION = 1
_GIZMO_ID = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_CALLBACK_LINE = re.compile(
    r"(?im)^\s*(knobChanged|onCreate|onDestroy|updateUI|autolabel|beforeRender|afterRender|"
    r"beforeFrameRender|afterFrameRender|onScriptLoad|onScriptSave|onScriptClose)\s+(.+?)\s*$"
)
_EXECUTABLE_KNOB = re.compile(
    r"(?i)addUserKnob\s*\{\s*22\b|\b(?:PyScript_Knob|PythonCustomKnob|PyCustom_Knob)\b|\[python\b"
)
_DEPENDENCY = re.compile(r"(?im)^\s*(file|font|lut|ocio_config)\s+(.+?)\s*$")
_KNOB_TYPES = {"float", "integer", "boolean", "string", "color"}
_NUKE_KNOB_TYPES = {
    "float": {"Double_Knob", "Scale_Knob", "Array_Knob", "WH_Knob"},
    "integer": {"Int_Knob", "Unsigned_Knob"},
    "boolean": {"Boolean_Knob"},
    "string": {"String_Knob", "File_Knob", "Multiline_Eval_String_Knob"},
    "color": {"Color_Knob", "AColor_Knob"},
}
_CONFLICT_POLICIES = {"fail", "replace_same_version", "write_versioned"}


def _plugin_root() -> Path:
    raw = os.environ.get(PLUGIN_ROOT_ENV, "").strip()
    if not raw:
        raise ValueError(f"{PLUGIN_ROOT_ENV} must name an isolated absolute plugin root")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f"{PLUGIN_ROOT_ENV} must be absolute")
    root = candidate.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _validate_id_version(gizmo_id: str, version: str | None = None) -> None:
    if not isinstance(gizmo_id, str) or not _GIZMO_ID.fullmatch(gizmo_id):
        raise ValueError("gizmo_id must be a lowercase dotted identifier")
    if version is not None and (not isinstance(version, str) or not _SEMVER.fullmatch(version)):
        raise ValueError("version must be semantic X.Y.Z")


def _class_name(gizmo_id: str) -> str:
    words = re.split(r"[._]", gizmo_id)
    readable = "".join(word[:1].upper() + word[1:] for word in words)
    suffix = hashlib.sha256(gizmo_id.encode("utf-8")).hexdigest()[:8]
    return f"DccMcp{readable}_{suffix}"


def _normalize_contract(
    gizmo_id: str,
    version: str,
    display_name: str,
    exposed_knobs: list[Mapping[str, Any]],
    input_contract: list[Mapping[str, Any]],
) -> dict[str, Any]:
    _validate_id_version(gizmo_id, version)
    if not isinstance(display_name, str) or not display_name.strip():
        raise ValueError("display_name must be a non-empty string")
    if not isinstance(exposed_knobs, list):
        raise ValueError("exposed_knobs must be an array")
    normalized_knobs = []
    names = set()
    for index, item in enumerate(exposed_knobs):
        if not isinstance(item, Mapping):
            raise ValueError(f"exposed_knobs[{index}] must be an object")
        name = item.get("name")
        target_node = item.get("target_node")
        target_knob = item.get("target_knob")
        kind = item.get("type")
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
            raise ValueError(f"exposed_knobs[{index}].name must be a Nuke identifier")
        if name in names:
            raise ValueError(f"duplicate exposed knob: {name}")
        names.add(name)
        if not isinstance(target_node, str) or not target_node or not isinstance(target_knob, str) or not target_knob:
            raise ValueError(f"exposed_knobs[{index}] requires target_node and target_knob")
        if kind not in _KNOB_TYPES:
            raise ValueError(f"exposed_knobs[{index}].type must be one of {sorted(_KNOB_TYPES)}")
        minimum = item.get("minimum")
        maximum = item.get("maximum")
        if kind in {"float", "integer", "color"}:
            if not isinstance(minimum, (int, float)) or isinstance(minimum, bool):
                raise ValueError(f"exposed_knobs[{index}].minimum must be numeric")
            if not isinstance(maximum, (int, float)) or isinstance(maximum, bool):
                raise ValueError(f"exposed_knobs[{index}].maximum must be numeric")
            if minimum > maximum:
                raise ValueError(f"exposed_knobs[{index}] minimum must not exceed maximum")
        default = _typed_value(name, kind, item.get("default"), item.get("minimum"), item.get("maximum"))
        normalized_knobs.append(
            {
                "name": name,
                "label": str(item.get("label") or name),
                "target_node": target_node,
                "target_knob": target_knob,
                "type": kind,
                "default": default,
                "minimum": item.get("minimum"),
                "maximum": item.get("maximum"),
            }
        )

    if not isinstance(input_contract, list):
        raise ValueError("input_contract must be an array")
    normalized_inputs = []
    input_names = set()
    for index, item in enumerate(input_contract):
        if not isinstance(item, Mapping):
            raise ValueError(f"input_contract[{index}] must be an object")
        name = item.get("name")
        required = item.get("required")
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
            raise ValueError(f"input_contract[{index}].name must be a Nuke identifier")
        if name in input_names:
            raise ValueError(f"duplicate input name: {name}")
        if not isinstance(required, bool):
            raise ValueError(f"input_contract[{index}].required must be boolean")
        input_names.add(name)
        normalized_inputs.append({"name": name, "required": required})
    return {
        "schema_version": SCHEMA_VERSION,
        "gizmo_id": gizmo_id,
        "version": version,
        "display_name": display_name.strip(),
        "node_class": _class_name(gizmo_id),
        "exposed_knobs": normalized_knobs,
        "input_contract": normalized_inputs,
    }


def _typed_value(name: str, kind: str, value: Any, minimum: Any, maximum: Any) -> Any:
    valid = {
        "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "string": lambda v: isinstance(v, str),
        "color": lambda v: (
            isinstance(v, (list, tuple))
            and len(v) in (3, 4)
            and all(isinstance(component, (int, float)) and not isinstance(component, bool) for component in v)
        ),
    }[kind]
    if not valid(value):
        raise ValueError(f"{name} must be {kind}")
    values = value if kind == "color" else [value]
    if minimum is not None and any(component < minimum for component in values):
        raise ValueError(f"{name} is below minimum {minimum}")
    if maximum is not None and any(component > maximum for component in values):
        raise ValueError(f"{name} exceeds maximum {maximum}")
    return list(value) if kind == "color" else value


def _asset_paths(root: Path, gizmo_id: str, version: str) -> tuple[Path, Path]:
    directory = root / gizmo_id / version
    return directory / f"{_class_name(gizmo_id)}.gizmo", directory / "manifest.json"


def _normalize_gizmo(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    normalized, count = re.subn(r"(?m)^(\s*)Group\s*\{", r"\1Gizmo {", text, count=1)
    if count != 1:
        raise ValueError("serialized Group header was not found")
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(normalized)


def _forbidden_code(text: str) -> list[str]:
    findings = {
        f"callback:{match.group(1)}" for match in _CALLBACK_LINE.finditer(text) if match.group(2).strip().strip('{}"')
    }
    findings.update(f"executable:{match.group(0).strip()[:80]}" for match in _EXECUTABLE_KNOB.finditer(text))
    return sorted(findings)


def _dependencies(text: str) -> list[dict[str, Any]]:
    dependencies = []
    for match in _DEPENDENCY.finditer(text):
        value = match.group(2).strip().strip('{}"')
        absolute = PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute()
        if absolute:
            dependencies.append({"kind": match.group(1), "path": value, "exists": Path(value).exists()})
    return dependencies


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _embedded_manifest(nuke: Any, manifest: Mapping[str, Any]) -> Any:
    knob = nuke.String_Knob("dcc_mcp_asset_manifest", "DCC MCP Asset Manifest")
    knob.setValue(json.dumps(dict(manifest), separators=(",", ":"), sort_keys=True))
    knob.setFlag(nuke.INVISIBLE)
    return knob


def create_from_group(
    nuke: Any,
    *,
    group_node: str,
    gizmo_id: str,
    version: str,
    display_name: str,
    exposed_knobs: list[Mapping[str, Any]],
    input_contract: list[Mapping[str, Any]],
    conflict_policy: str,
) -> dict[str, Any]:
    manifest = _normalize_contract(gizmo_id, version, display_name, exposed_knobs, input_contract)
    if conflict_policy not in _CONFLICT_POLICIES:
        raise ValueError(f"conflict_policy must be one of {sorted(_CONFLICT_POLICIES)}")
    root = _plugin_root()
    target, manifest_path = _asset_paths(root, gizmo_id, version)
    existing_versions = list((root / gizmo_id).glob("*/manifest.json"))
    if conflict_policy == "fail" and existing_versions:
        raise ValueError(f"gizmo_id already exists: {gizmo_id}")
    if conflict_policy == "write_versioned" and target.exists():
        raise ValueError(f"Gizmo version already exists: {gizmo_id} {version}")

    group = nuke.toNode(group_node)
    if group is None or group.Class() != "Group":
        raise ValueError("group_node must name an existing Group")
    children = list(group.nodes())
    inputs = [node for node in children if node.Class() == "Input"]
    if len(inputs) != len(manifest["input_contract"]):
        raise ValueError(
            f"input_contract declares {len(manifest['input_contract'])} inputs but Group contains {len(inputs)}"
        )
    children_by_name = {node.name(): node for node in children}
    for spec in manifest["exposed_knobs"]:
        target_node = children_by_name.get(spec["target_node"])
        target_knob = target_node.knob(spec["target_knob"]) if target_node is not None else None
        if target_knob is None:
            raise ValueError(f"exposed knob target not found: {spec['target_node']}.{spec['target_knob']}")
        knob_class = target_knob.Class() if hasattr(target_knob, "Class") else None
        if knob_class and knob_class not in _NUKE_KNOB_TYPES[spec["type"]]:
            raise ValueError(
                f"exposed knob type mismatch: {spec['target_node']}.{spec['target_knob']} is {knob_class}, "
                f"not {spec['type']}"
            )

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.dcc-mcp.tmp")
    temporary.unlink(missing_ok=True)
    original_name = group.name()
    label_knob = group.knob("label")
    original_label = label_knob.value() if label_knob is not None else None
    top_nodes = list(nuke.allNodes(recurseGroups=False))
    selected = {node: bool(node.knob("selected").value()) for node in top_nodes if node.knob("selected") is not None}
    added = []
    changed_targets = []
    try:
        group.setName(manifest["node_class"])
        if label_knob is not None:
            label_knob.setValue(manifest["display_name"])
        for spec in manifest["exposed_knobs"]:
            target_knob = children_by_name[spec["target_node"]].knob(spec["target_knob"])
            changed_targets.append((target_knob, target_knob.value()))
            target_knob.setValue(spec["default"])
            link = nuke.Link_Knob(spec["name"], spec["label"])
            link.makeLink(spec["target_node"], spec["target_knob"])
            if spec["minimum"] is not None and spec["maximum"] is not None and hasattr(link, "setRange"):
                link.setRange(spec["minimum"], spec["maximum"])
            group.addKnob(link)
            added.append(link)
        metadata = _embedded_manifest(nuke, manifest)
        group.addKnob(metadata)
        added.append(metadata)
        for node in top_nodes:
            node.setSelected(False)
        group.setSelected(True)
        if not nuke.nodeCopy(str(temporary)):
            raise RuntimeError("Nuke did not serialize the Group")
        _normalize_gizmo(temporary)
        forbidden = _forbidden_code(temporary.read_text(encoding="utf-8"))
        if forbidden:
            raise ValueError(f"forbidden callback/code found: {', '.join(forbidden)}")
        digest = _sha256(temporary)
        temporary.replace(target)
        published = {**manifest, "sha256": digest, "gizmo_path": str(target)}
        temporary_manifest = manifest_path.with_name(".manifest.json.dcc-mcp.tmp")
        temporary_manifest.write_text(json.dumps(published, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary_manifest.replace(manifest_path)
    finally:
        temporary.unlink(missing_ok=True)
        for knob in reversed(added):
            if hasattr(group, "removeKnob"):
                group.removeKnob(knob)
        for knob, value in changed_targets:
            knob.setValue(value)
        group.setName(original_name)
        if label_knob is not None:
            label_knob.setValue(original_label)
        for node, was_selected in selected.items():
            node.setSelected(was_selected)

    return {
        "gizmo_id": gizmo_id,
        "version": version,
        "node_class": manifest["node_class"],
        "gizmo_path": str(target),
        "manifest_path": str(manifest_path),
        "sha256": digest,
        "input_contract": manifest["input_contract"],
        "public_knobs": manifest["exposed_knobs"],
    }


def _read_asset(gizmo_id: str, version: str | None) -> tuple[dict[str, Any], Path, Path]:
    _validate_id_version(gizmo_id, version)
    root = _plugin_root()
    if version is None:
        candidates = (
            [path.name for path in (root / gizmo_id).iterdir() if path.is_dir() and _SEMVER.fullmatch(path.name)]
            if (root / gizmo_id).is_dir()
            else []
        )
        if not candidates:
            raise ValueError(f"Gizmo is not registered: {gizmo_id}")
        version = max(candidates, key=lambda item: tuple(int(part) for part in item.split(".")))
    target, manifest_path = _asset_paths(root, gizmo_id, version)
    if not target.is_file() or not manifest_path.is_file():
        raise ValueError(f"Gizmo is not registered: {gizmo_id} {version}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("gizmo_id") != gizmo_id
        or manifest.get("version") != version
    ):
        raise ValueError("Gizmo manifest identity is invalid")
    digest = _sha256(target)
    if digest != manifest.get("sha256"):
        raise ValueError("Gizmo hash does not match its manifest")
    forbidden = _forbidden_code(target.read_text(encoding="utf-8"))
    if forbidden:
        raise ValueError(f"forbidden callback/code found: {', '.join(forbidden)}")
    return manifest, target, manifest_path


def _validate_overrides(manifest: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    specs = {item["name"]: item for item in manifest["exposed_knobs"]}
    unknown = sorted(set(overrides) - set(specs))
    if unknown:
        raise ValueError(f"Gizmo knobs not exposed: {', '.join(unknown)}")
    return {
        name: _typed_value(name, specs[name]["type"], value, specs[name]["minimum"], specs[name]["maximum"])
        for name, value in overrides.items()
    }


def _read_node_manifest(node: Any) -> dict[str, Any]:
    knob = node.knob("dcc_mcp_asset_manifest")
    if knob is None:
        return {}
    try:
        return json.loads(knob.value())
    except (TypeError, ValueError):
        return {}


def instantiate(
    nuke: Any,
    *,
    gizmo_id: str,
    version: str | None = None,
    input_nodes: Mapping[str, str] | None = None,
    knob_overrides: Mapping[str, Any] | None = None,
    node_name: str | None = None,
    xpos: int | None = None,
    ypos: int | None = None,
) -> dict[str, Any]:
    manifest, target, _ = _read_asset(gizmo_id, version)
    if node_name is not None and (not isinstance(node_name, str) or not _IDENTIFIER.fullmatch(node_name)):
        raise ValueError("node_name must be a Nuke identifier")
    bindings = dict(input_nodes or {})
    contract = {item["name"]: (index, item) for index, item in enumerate(manifest["input_contract"])}
    unknown_inputs = sorted(set(bindings) - set(contract))
    if unknown_inputs:
        raise ValueError(f"Gizmo inputs not declared: {', '.join(unknown_inputs)}")
    missing_inputs = [
        item["name"] for item in manifest["input_contract"] if item["required"] and item["name"] not in bindings
    ]
    if missing_inputs:
        raise ValueError(f"required Gizmo inputs missing: {', '.join(missing_inputs)}")
    resolved_inputs = {}
    for name, source_name in bindings.items():
        source = nuke.toNode(source_name)
        if source is None:
            raise ValueError(f"input node not found: {source_name}")
        resolved_inputs[name] = source
    values = _validate_overrides(manifest, dict(knob_overrides or {}))

    existing = nuke.toNode(node_name) if node_name else None
    reused = existing is not None
    if existing is not None:
        identity = _read_node_manifest(existing)
        if (
            identity.get("gizmo_id") != gizmo_id
            or identity.get("version") != manifest["version"]
            or existing.Class() != manifest["node_class"]
        ):
            raise ValueError(f"node_name already belongs to another node: {node_name}")
        node = existing
    else:
        nuke.pluginAddPath(str(target.parent))
        nuke.load(manifest["node_class"])
        node = nuke.createNode(manifest["node_class"], inpanel=False)
        identity = _read_node_manifest(node)
        if identity.get("gizmo_id") != gizmo_id or identity.get("version") != manifest["version"]:
            nuke.delete(node)
            raise RuntimeError("loaded Gizmo identity does not match its registry manifest")
        if node_name:
            node.setName(node_name)
    try:
        for name, source in resolved_inputs.items():
            node.setInput(contract[name][0], source)
        for name, value in values.items():
            knob = node.knob(name)
            if knob is None:
                raise RuntimeError(f"Gizmo public knob is missing: {name}")
            knob.setValue(value)
        if xpos is not None or ypos is not None:
            current_x = node.xpos() if xpos is None and hasattr(node, "xpos") else int(xpos or 0)
            current_y = node.ypos() if ypos is None and hasattr(node, "ypos") else int(ypos or 0)
            node.setXYpos(current_x, current_y)
    except Exception:
        if not reused:
            nuke.delete(node)
        raise
    return {
        "gizmo_id": gizmo_id,
        "version": manifest["version"],
        "node_name": node.name(),
        "node_class": node.Class(),
        "reused": reused,
        "applied_knobs": sorted(values),
        "bound_inputs": sorted(bindings),
        "sha256": manifest["sha256"],
    }


def _channel_report(nuke: Any, source: Any, output: Any, frame: int) -> dict[str, Any]:
    source_channels = list(source.channels())
    output_channels = list(output.channels())
    preserved = [channel for channel in source_channels if channel not in {"rgba.red", "rgba.green", "rgba.blue"}]
    missing = sorted(set(preserved) - set(output_channels))
    mismatches = []
    finite = True
    if hasattr(nuke, "sample"):
        old_frame = nuke.frame() if hasattr(nuke, "frame") else None
        if hasattr(nuke, "frame"):
            nuke.frame(frame)
        try:
            width = max(1, int(source.width()))
            height = max(1, int(source.height()))
            points = [(width * 0.25, height * 0.25), (width * 0.5, height * 0.5), (width * 0.75, height * 0.75)]
            for channel in preserved:
                if channel not in output_channels:
                    continue
                for x, y in points:
                    before = nuke.sample(source, channel, x, y, 1, 1)
                    after = nuke.sample(output, channel, x, y, 1, 1)
                    if before != after:
                        mismatches.append({"channel": channel, "x": x, "y": y, "before": before, "after": after})
            for channel in ("rgba.red", "rgba.green", "rgba.blue", "rgba.alpha"):
                if channel in output_channels:
                    finite = finite and all(math.isfinite(nuke.sample(output, channel, x, y, 1, 1)) for x, y in points)
        finally:
            if old_frame is not None:
                nuke.frame(old_frame)
    return {
        "checked": True,
        "source_channel_count": len(source_channels),
        "output_channel_count": len(output_channels),
        "missing_channels": missing,
        "sample_mismatches": mismatches,
        "alpha_preserved": "rgba.alpha" not in missing
        and not any(item["channel"] == "rgba.alpha" for item in mismatches),
        "cryptomatte_preserved": not any(channel.lower().startswith("crypto") for channel in missing),
        "finite_output_samples": finite,
    }


def validate(
    nuke: Any,
    gizmo_id: str,
    version: str | None = None,
    input_nodes: Mapping[str, str] | None = None,
    representative_frame: int = 1,
    render_output_path: str | None = None,
) -> dict[str, Any]:
    manifest, target, manifest_path = _read_asset(gizmo_id, version)
    text = target.read_text(encoding="utf-8")
    forbidden = _forbidden_code(text)
    dependencies = _dependencies(text)
    node = None
    clean_load = {"passed": False, "mode": "isolated temporary instance", "error": None}
    child_nodes = []
    channel_preservation = {"checked": False}
    render = {"performed": False, "output_path": None, "sha256": None}
    issues = []
    try:
        nuke.pluginAddPath(str(target.parent))
        nuke.load(manifest["node_class"])
        node = nuke.createNode(manifest["node_class"], inpanel=False)
        identity = _read_node_manifest(node)
        if identity.get("gizmo_id") != gizmo_id or identity.get("version") != manifest["version"]:
            raise RuntimeError("loaded Gizmo identity does not match its registry manifest")
        clean_load["passed"] = True
        children = list(node.nodes()) if hasattr(node, "nodes") else []
        child_nodes = [{"name": child.name(), "class": child.Class()} for child in children]
        if input_nodes:
            contract = {item["name"]: index for index, item in enumerate(manifest["input_contract"])}
            for name, source_name in input_nodes.items():
                if name not in contract:
                    raise ValueError(f"Gizmo input not declared: {name}")
                source = nuke.toNode(source_name)
                if source is None:
                    raise ValueError(f"input node not found: {source_name}")
                node.setInput(contract[name], source)
            primary = manifest["input_contract"][0]["name"] if manifest["input_contract"] else None
            if primary and primary in input_nodes:
                channel_preservation = _channel_report(
                    nuke, nuke.toNode(input_nodes[primary]), node, representative_frame
                )
                if channel_preservation["missing_channels"] or channel_preservation["sample_mismatches"]:
                    issues.append("channel preservation failed")
                if not channel_preservation["finite_output_samples"]:
                    issues.append("representative output contains NaN or Inf")
        if render_output_path:
            output = Path(render_output_path).expanduser()
            if not output.is_absolute():
                raise ValueError("render_output_path must be absolute")
            output.parent.mkdir(parents=True, exist_ok=True)
            write = nuke.nodes.Write(file=str(output).replace("\\", "/"))
            write.setInput(0, node)
            try:
                nuke.execute(write, representative_frame, representative_frame)
            finally:
                nuke.delete(write)
            render = {
                "performed": True,
                "output_path": str(output.resolve()),
                "sha256": _sha256(output) if output.is_file() else None,
            }
            if render["sha256"] is None:
                issues.append("representative render did not produce an output")
    except Exception as exc:
        clean_load["error"] = str(exc)
        issues.append(str(exc))
    finally:
        if node is not None:
            nuke.delete(node)
    if forbidden:
        issues.append("forbidden callback/code found")
    if dependencies:
        issues.append("absolute dependencies found")
    return {
        "valid": not issues,
        "schema_version": manifest["schema_version"],
        "gizmo_id": gizmo_id,
        "version": manifest["version"],
        "node_class": manifest["node_class"],
        "gizmo_path": str(target),
        "manifest_path": str(manifest_path),
        "sha256": manifest["sha256"],
        "input_contract": manifest["input_contract"],
        "public_knobs": manifest["exposed_knobs"],
        "internal_node_count": len(child_nodes),
        "child_nodes": child_nodes,
        "dependencies": dependencies,
        "forbidden_code": forbidden,
        "clean_load": clean_load,
        "representative_render": render,
        "channel_preservation": channel_preservation,
        "issues": issues,
    }
