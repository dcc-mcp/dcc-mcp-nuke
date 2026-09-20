"""CI audit for companion tool names embedded in agent-facing descriptions."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from dcc_mcp_core import ToolRegistry, register_script_materialization_tools

_TOOL_REFERENCE = re.compile(r"`?([a-z][a-z0-9_-]+)`?\s+tools?\b", re.IGNORECASE)


def _normalize_tool_name(value: str) -> str:
    return value.strip("`").lower().replace("-", "_")


def _core_actions() -> list[dict[str, Any]]:
    registry = ToolRegistry()
    server = SimpleNamespace(registry=registry, register_handler=lambda *_args, **_kwargs: None)
    registered = register_script_materialization_tools(server, dcc_name="nuke")
    if registered != 1:
        raise RuntimeError("Core materialize_script registration is unavailable")
    return list(registry.list_actions())


def _manifest_actions(skills_root: Path) -> tuple[list[dict[str, Any]], set[str]]:
    actions: list[dict[str, Any]] = []
    names: set[str] = set()
    for manifest_path in sorted(skills_root.glob("*/tools.yaml")):
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        skill_name = manifest_path.parent.name.replace("-", "_")
        for tool in payload.get("tools", []):
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                continue
            name = _normalize_tool_name(tool["name"])
            names.update({name, f"{skill_name}__{name}"})
            actions.append(
                {
                    "name": name,
                    "description": str(tool.get("description", "")),
                    "source": str(manifest_path),
                }
            )
    return actions, names


def audit_host_flavor_metadata(skills_root: Path) -> list[str]:
    """Return errors for skills that declare an unknown host flavor.

    A bundled skill may restrict itself with ``metadata.dcc-mcp.host-flavors``.
    Only values in :data:`dcc_mcp_nuke.host_flavor.HOST_FLAVORS` are meaningful;
    anything else would silently widen or narrow the gate, so it fails CI.
    """
    from dcc_mcp_nuke.host_flavor import HOST_FLAVORS, SKILL_METADATA_KEY, normalize_host_flavors

    issues: list[str] = []
    for manifest_path in sorted(Path(skills_root).glob("*/SKILL.md")):
        skill_name = manifest_path.parent.name
        payload = _frontmatter(manifest_path)
        metadata = payload.get("metadata") or {}
        dcc_mcp = metadata.get("dcc-mcp") if isinstance(metadata, dict) else None
        if not isinstance(dcc_mcp, dict) or SKILL_METADATA_KEY not in dcc_mcp:
            continue
        declared = dcc_mcp[SKILL_METADATA_KEY]
        normalized = normalize_host_flavors(declared)
        if not normalized:
            issues.append(
                f"{skill_name} declares metadata.dcc-mcp.{SKILL_METADATA_KEY} without a "
                f"recognized flavor: {declared!r} (expected one of {list(HOST_FLAVORS)})"
            )
    return issues


def _frontmatter(skill_md: Path) -> dict:
    """Return the parsed YAML frontmatter of a SKILL.md, or ``{}``."""
    lines = skill_md.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            payload = yaml.safe_load("\n".join(lines[1:index]))
            return payload if isinstance(payload, dict) else {}
    return {}


def audit_companion_references(skills_root: Path) -> list[str]:
    """Return errors for description references that lack registered tools."""
    actions, registered_names = _manifest_actions(Path(skills_root))
    core_actions = _core_actions()
    registered_names.update(_normalize_tool_name(action["name"]) for action in core_actions)
    issues: list[str] = []
    for action in [*core_actions, *actions]:
        description = str(action.get("description", ""))
        for match in _TOOL_REFERENCE.finditer(description):
            companion = _normalize_tool_name(match.group(1))
            if companion not in registered_names:
                issues.append(f"{action['name']} description references unregistered companion tool {companion}")
    return issues


__all__ = ["audit_companion_references", "audit_host_flavor_metadata"]
