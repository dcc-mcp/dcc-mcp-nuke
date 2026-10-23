"""Non-authorizing API observations, separate from legacy host-flavor labels."""

from __future__ import annotations

import sys
from typing import Any


def runtime_evidence() -> dict:
    """Inspect already loaded modules without importing hosts or calling their APIs.

    A callable attribute is not proof of a usable license, successful execution,
    or a supported standalone Hiero bootstrap. This report never changes gates.
    """
    probes = {
        "nuke.node_graph": ("nuke", ("createNode", "toNode")),
        "nuke.render": ("nuke", ("execute",)),
        "hiero.project_inspection": ("hiero.core", ("projects",)),
    }
    surfaces = {}
    for name, (module_name, attributes) in probes.items():
        module = sys.modules.get(module_name)
        available = []
        unavailable = []
        for attribute in attributes:
            try:
                value: Any = getattr(module, attribute, None)
                present = callable(value)
            except Exception:
                present = False
            (available if present else unavailable).append(attribute)
        surfaces[name] = {
            "module": module_name,
            "state": "not_loaded" if module is None else "observed" if not unavailable else "incomplete",
            "callable_attributes": available,
            "unobserved_attributes": unavailable,
        }
    return {
        "scope": "already_loaded_module_attributes_only",
        "surfaces": surfaces,
        "license_status": "unknown",
        "native_verification": "not_performed",
        "authorizes_execution": False,
    }
