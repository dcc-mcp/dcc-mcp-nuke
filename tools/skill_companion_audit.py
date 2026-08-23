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


__all__ = ["audit_companion_references"]
