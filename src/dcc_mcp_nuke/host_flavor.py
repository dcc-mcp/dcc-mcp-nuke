"""Host flavor detection and capability gating for Nuke, NukeX, and Nuke Studio.

Nuke, NukeX, and Nuke Studio ship from one Foundry installation and share one
embedded Python interpreter and one ``~/.nuke`` plug-in profile. Core already
models them as executable stems of a single ``dcc_kind`` (``nuke``), so this
adapter stays one package with one ``dcc_type``. What *does* differ between the
three entry points is the feature surface available at runtime: only Nuke Studio
exposes the Hiero timeline, sequence, project bin, and conform APIs.

This module turns that difference into a first-class, testable contract:

* :func:`detect_host_flavor` classifies the running host as ``nuke``, ``nukex``,
  or ``nukestudio``.
* :func:`capabilities_for_flavor` maps a flavor to its capability set.
* :func:`skill_host_flavors` reads the host flavors a bundled skill declares, and
  :func:`missing_capability_error` builds the explicit failure an agent sees when
  a Studio-only skill is invoked on a non-Studio host.

Detection never raises: an unreadable signal degrades to the next one and,
finally, to the ``nuke`` baseline. A wrong ``nuke`` classification hides
Studio skills; a wrong ``nukestudio`` classification would expose tools whose
APIs do not exist, so ambiguous Hiero evidence is treated as a *supporting*
signal only.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple

FLAVOR_NUKE = "nuke"
FLAVOR_NUKE_X = "nukex"
FLAVOR_NUKE_STUDIO = "nukestudio"

#: Closed vocabulary of host flavors this adapter can report.
HOST_FLAVORS: Tuple[str, ...] = (FLAVOR_NUKE, FLAVOR_NUKE_X, FLAVOR_NUKE_STUDIO)

#: Operator/test override. An unrecognized value is ignored, not fatal.
ENV_FLAVOR_OVERRIDE = "DCC_MCP_NUKE_HOST_FLAVOR"

#: SKILL.md frontmatter key, read through ``metadata.dcc-mcp`` pass-through.
SKILL_METADATA_KEY = "host-flavors"

CAPABILITY_COMPOSITING = "compositing"
CAPABILITY_NODE_GRAPH = "node_graph"
CAPABILITY_SCRIPTING = "scripting"

CAPABILITY_STUDIO_TIMELINE = "studio.timeline"
CAPABILITY_STUDIO_SEQUENCE = "studio.sequence"
CAPABILITY_STUDIO_PROJECT_BIN = "studio.project_bin"
CAPABILITY_STUDIO_CONFORM = "studio.conform"
CAPABILITY_STUDIO_TRACK = "studio.track"

#: Capabilities every Nuke-family entry point provides.
BASE_CAPABILITIES: Tuple[str, ...] = (
    CAPABILITY_COMPOSITING,
    CAPABILITY_NODE_GRAPH,
    CAPABILITY_SCRIPTING,
)

#: Incremental surface Nuke Studio adds on top of the shared baseline.
STUDIO_CAPABILITIES: Tuple[str, ...] = (
    CAPABILITY_STUDIO_TIMELINE,
    CAPABILITY_STUDIO_SEQUENCE,
    CAPABILITY_STUDIO_PROJECT_BIN,
    CAPABILITY_STUDIO_CONFORM,
    CAPABILITY_STUDIO_TRACK,
)

FLAVOR_CAPABILITIES: dict = {
    FLAVOR_NUKE: frozenset(BASE_CAPABILITIES),
    FLAVOR_NUKE_X: frozenset(BASE_CAPABILITIES),
    FLAVOR_NUKE_STUDIO: frozenset(BASE_CAPABILITIES) | frozenset(STUDIO_CAPABILITIES),
}

#: ``nuke.env`` keys whose truthy value marks a Nuke Studio session.
_STUDIO_ENV_KEYS: Tuple[str, ...] = ("studio", "nukestudio", "nuke_studio", "hiero")
#: ``nuke.env`` keys whose truthy value marks a NukeX session.
_NUKEX_ENV_KEYS: Tuple[str, ...] = ("nukex",)

_UNKNOWN_HOST_VERSION = "unknown"


@dataclass(frozen=True)
class HostFlavorReport:
    """Immutable result of one host flavor classification."""

    flavor: str
    gui: bool
    host_version: str
    executable: str
    hiero_importable: bool
    signals: Tuple[str, ...]
    capabilities: Tuple[str, ...]

    def to_dict(self) -> dict:
        """Return a JSON-serializable view of the report."""
        return {
            "flavor": self.flavor,
            "gui": self.gui,
            "host_version": self.host_version,
            "executable": self.executable,
            "hiero_importable": self.hiero_importable,
            "signals": list(self.signals),
            "capabilities": list(self.capabilities),
        }

    def to_instance_metadata(self) -> dict:
        """Return flat string metadata for gateway instance registration."""
        return {
            "host_flavor": self.flavor,
            "host_flavor_capabilities": ",".join(self.capabilities),
            "host_flavor_gui": "true" if self.gui else "false",
            "host_flavor_signals": ",".join(self.signals),
        }


def _resolve_nuke_module(nuke_module: Optional[Any]) -> Optional[Any]:
    """Return the caller's module, an already-imported ``nuke``, or ``None``."""
    if nuke_module is not None:
        return nuke_module
    cached = sys.modules.get("nuke")
    if cached is not None:
        return cached
    try:
        import nuke as imported  # type: ignore[import-not-found]
    except Exception:
        return None
    return imported


def _env_mapping(nuke_module: Optional[Any]) -> Mapping[str, Any]:
    env = getattr(nuke_module, "env", None)
    return env if isinstance(env, Mapping) else {}


def _flag(mapping: Mapping[str, Any], keys: Sequence[str]) -> bool:
    """Return True when any matching key holds a truthy, non-"false" value.

    ``nuke.env`` mixes booleans with strings across releases, so compare
    case-insensitively and treat explicit ``0``/``false``/empty as unset.
    """
    for key in keys:
        for actual in mapping:
            if not isinstance(actual, str) or actual.lower() != key.lower():
                continue
            value = mapping[actual]
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in ("", "0", "false", "none", "off"):
                    continue
                return True
            if value:
                return True
    return False


def _executable_name() -> str:
    """Return the lowercased basename of the running interpreter, if any."""
    for candidate in (sys.executable, sys.argv[0] if sys.argv else ""):
        name = os.path.basename(str(candidate or "")).lower()
        if name:
            return name
    return ""


def _hiero_importable() -> bool:
    """Return True when the Hiero Python package resolves in this interpreter."""
    try:
        return importlib.util.find_spec("hiero") is not None
    except Exception:
        return False


def _flavor_override() -> Tuple[Optional[str], Tuple[str, ...]]:
    """Read the operator/test override, ignoring unrecognized values."""
    raw = os.environ.get(ENV_FLAVOR_OVERRIDE, "").strip().lower()
    if not raw:
        return None, ()
    if raw in HOST_FLAVORS:
        return raw, (f"env-override:{raw}",)
    return None, (f"env-override-ignored:{raw}",)


def detect_host_flavor(nuke_module: Optional[Any] = None) -> HostFlavorReport:
    """Classify the running Nuke host as ``nuke``, ``nukex``, or ``nukestudio``.

    Signals are evaluated most-decisive first:

    1. ``DCC_MCP_NUKE_HOST_FLAVOR`` operator/test override.
    2. Studio keys in ``nuke.env`` (``studio``, ``nukestudio``, ``hiero``, …).
    3. A ``studio`` substring in the executable name (``NukeStudio16.0``).
    4. ``nukex`` in ``nuke.env``.
    5. A ``nukex`` substring in the executable name (``NukeX16.0``).
    6. The ``nuke`` baseline.

    ``hiero`` importability is reported in :attr:`HostFlavorReport.hiero_importable`
    for diagnostics but never selects a flavor: a non-Studio host can expose an
    importable ``hiero`` module without the Studio capability surface, and
    classifying it as ``nukestudio`` would unlock Studio-only tools whose APIs do
    not exist. Falling back to ``nuke`` only hides those skills.

    Studio is evaluated before NukeX because Nuke Studio is a superset that may
    also advertise NukeX features.
    """
    module = _resolve_nuke_module(nuke_module)
    env = _env_mapping(module)
    executable = _executable_name()
    hiero_importable = _hiero_importable()

    override, override_signals = _flavor_override()
    if override is not None:
        return _report(override, env, executable, hiero_importable, override_signals)

    signals = ()
    if _flag(env, _STUDIO_ENV_KEYS):
        return _report(FLAVOR_NUKE_STUDIO, env, executable, hiero_importable, override_signals + ("nuke.env:studio",))
    if "studio" in executable:
        return _report(FLAVOR_NUKE_STUDIO, env, executable, hiero_importable, override_signals + ("executable:studio",))
    if _flag(env, _NUKEX_ENV_KEYS):
        return _report(FLAVOR_NUKE_X, env, executable, hiero_importable, override_signals + ("nuke.env:nukex",))
    if "nukex" in executable:
        return _report(FLAVOR_NUKE_X, env, executable, hiero_importable, override_signals + ("executable:nukex",))
    signals = override_signals + ("baseline",)
    return _report(FLAVOR_NUKE, env, executable, hiero_importable, signals)


def _report(
    flavor: str,
    env: Mapping[str, Any],
    executable: str,
    hiero_importable: bool,
    signals: Tuple[str, ...],
) -> HostFlavorReport:
    version = env.get("NukeVersionString") or env.get("NukeVersion") or _UNKNOWN_HOST_VERSION
    return HostFlavorReport(
        flavor=flavor,
        gui=bool(env.get("gui")),
        host_version=str(version),
        executable=executable,
        hiero_importable=hiero_importable,
        signals=tuple(signals),
        capabilities=_sorted_capabilities(flavor),
    )


def _sorted_capabilities(flavor: str) -> Tuple[str, ...]:
    return tuple(sorted(FLAVOR_CAPABILITIES.get(flavor, frozenset(BASE_CAPABILITIES))))


def capabilities_for_flavor(flavor: str) -> frozenset:
    """Return the capability set advertised by ``flavor``."""
    return FLAVOR_CAPABILITIES.get(flavor, frozenset(BASE_CAPABILITIES))


def supports_capability(flavor: str, capability: str) -> bool:
    """Return True when ``flavor`` advertises ``capability``."""
    return capability in capabilities_for_flavor(flavor)


def normalize_host_flavors(value: Any) -> Tuple[str, ...]:
    """Normalize a declared flavor list, returning only recognized flavors.

    Accepts a list, a comma-separated string, or a single flavor string.
    Unknown entries are dropped so a typo cannot silently widen a gate.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        raw_items = [part.strip().lower() for part in value.split(",")]
    elif isinstance(value, Sequence):
        raw_items = [str(item).strip().lower() for item in value]
    else:
        raw_items = [str(value).strip().lower()]
    return tuple(item for item in raw_items if item and item in HOST_FLAVORS)


def skill_host_flavors(skill_metadata: Any) -> Tuple[str, ...]:
    """Return the host flavors a skill declares, or ``()`` when unrestricted.

    Core's Rust skill loader preserves unknown ``metadata.dcc-mcp`` keys
    verbatim in the metadata mapping, so the adapter-owned ``host-flavors`` key
    survives discovery without a core change.
    """
    metadata = getattr(skill_metadata, "metadata", None)
    if not isinstance(metadata, Mapping):
        return ()
    for key, value in metadata.items():
        if isinstance(key, str) and key.lower().endswith(SKILL_METADATA_KEY):
            return normalize_host_flavors(value)
    return ()


def skill_allowed_on_flavor(skill_metadata: Any, flavor: str) -> bool:
    """Return True when ``skill_metadata`` may load on ``flavor``."""
    declared = skill_host_flavors(skill_metadata)
    if not declared:
        return True
    return flavor in declared


def missing_capability_error(
    *,
    tool_name: str,
    flavor: str,
    required: Sequence[str],
    skill_name: str = "",
) -> dict:
    """Build the explicit capability-missing result for a gated tool call.

    Returned through the skill result envelope so a caller sees a stable
    ``capability_unavailable`` error instead of a silent no-op.
    """
    from dcc_mcp_core.skill import skill_error

    required_text = ", ".join(required) or "unspecified"
    return skill_error(
        f"{tool_name} requires a Nuke Studio host",
        "capability_unavailable",
        prompt=(
            "Run this tool in Nuke Studio, or use the shared Nuke compositing "
            "skills for work that does not need the timeline surface."
        ),
        possible_solutions=[
            "Start Nuke Studio (the bundled NukeX/Hiero entry point) and retry.",
            "Set DCC_MCP_NUKE_HOST_FLAVOR=nukestudio only when the host really is Nuke Studio.",
        ],
        tool_name=tool_name,
        skill_name=skill_name,
        host_flavor=flavor,
        required_host_flavors=list(required),
        required_capabilities=sorted(
            {cap for flavor_name in required for cap in capabilities_for_flavor(flavor_name)} & set(STUDIO_CAPABILITIES)
        ),
        message_detail=f"host flavor {flavor!r} does not provide: {required_text}",
    )


class HostFlavorGate:
    """Veto skill loads whose declared host flavors exclude the running host.

    Installed on the server through core's ``set_skill_load_transform`` so the
    gate reuses the existing progressive-skill and ``load-skill`` activation
    path: a gated skill stays discovered but unloadable, and any load attempt
    raises instead of registering tools that cannot work.
    """

    def __init__(self, report: Optional[HostFlavorReport] = None) -> None:
        self._report = report or detect_host_flavor()

    @property
    def report(self) -> HostFlavorReport:
        return self._report

    @property
    def flavor(self) -> str:
        return self._report.flavor

    def declared_flavors(self, skill_metadata: Any) -> Tuple[str, ...]:
        return skill_host_flavors(skill_metadata)

    def allows(self, skill_metadata: Any) -> bool:
        return skill_allowed_on_flavor(skill_metadata, self.flavor)

    def veto_message(self, skill_metadata: Any) -> str:
        name = getattr(skill_metadata, "name", "<unknown>")
        declared = ", ".join(self.declared_flavors(skill_metadata))
        return (
            f"skill {name!r} requires host flavor(s) [{declared}] but this session is "
            f"{self.flavor!r}; Studio-only skills stay hidden outside Nuke Studio"
        )

    def __call__(self, skill_metadata: Any) -> None:
        """Raise :class:`PermissionError` to veto a load on the wrong host."""
        if not self.allows(skill_metadata):
            raise PermissionError(self.veto_message(skill_metadata))
        return None


__all__ = [
    "BASE_CAPABILITIES",
    "CAPABILITY_COMPOSITING",
    "CAPABILITY_NODE_GRAPH",
    "CAPABILITY_SCRIPTING",
    "CAPABILITY_STUDIO_CONFORM",
    "CAPABILITY_STUDIO_PROJECT_BIN",
    "CAPABILITY_STUDIO_SEQUENCE",
    "CAPABILITY_STUDIO_TIMELINE",
    "CAPABILITY_STUDIO_TRACK",
    "ENV_FLAVOR_OVERRIDE",
    "FLAVOR_NUKE",
    "FLAVOR_NUKE_STUDIO",
    "FLAVOR_NUKE_X",
    "FLAVOR_CAPABILITIES",
    "HOST_FLAVORS",
    "HostFlavorGate",
    "HostFlavorReport",
    "SKILL_METADATA_KEY",
    "STUDIO_CAPABILITIES",
    "capabilities_for_flavor",
    "detect_host_flavor",
    "missing_capability_error",
    "normalize_host_flavors",
    "skill_allowed_on_flavor",
    "skill_host_flavors",
    "supports_capability",
]
