"""Declarative entry point for the policy-bound Nuke Python executor."""

from __future__ import annotations

from typing import Any

from dcc_mcp_core.skill import skill_entry

from dcc_mcp_nuke.scripting import execute_python


@skill_entry
def main(**params: Any) -> dict[str, Any]:
    return execute_python(**params)
