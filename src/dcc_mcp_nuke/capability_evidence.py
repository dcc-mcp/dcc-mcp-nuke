"""Non-authorizing API observations, separate from legacy host-flavor labels."""

from __future__ import annotations

import sys
from types import ModuleType


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
        # Bypass module __getattr__/__getattribute__: lazy host hooks may import
        # code or initialize a host merely when an absent attribute is requested.
        namespace = ModuleType.__getattribute__(module, "__dict__") if isinstance(module, ModuleType) else {}
        available = []
        unavailable = []
        for attribute in attributes:
            present = callable(namespace.get(attribute))
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
