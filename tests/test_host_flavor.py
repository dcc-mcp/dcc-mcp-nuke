from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from dcc_mcp_nuke.host_flavor import (
    CAPABILITY_STUDIO_TIMELINE,
    ENV_FLAVOR_OVERRIDE,
    FLAVOR_NUKE,
    FLAVOR_NUKE_STUDIO,
    FLAVOR_NUKE_X,
    HOST_FLAVORS,
    STUDIO_CAPABILITIES,
    HostFlavorGate,
    capabilities_for_flavor,
    detect_host_flavor,
    missing_capability_error,
    normalize_host_flavors,
    skill_allowed_on_flavor,
    skill_host_flavors,
    supports_capability,
)

SKILLS_ROOT = Path(__file__).parents[1] / "src" / "dcc_mcp_nuke" / "skills"


def fake_nuke(**env):
    return SimpleNamespace(env=dict(env))


def test_flavor_vocabulary_is_closed_and_ordered():
    assert HOST_FLAVORS == (FLAVOR_NUKE, FLAVOR_NUKE_X, FLAVOR_NUKE_STUDIO)


def test_studio_flavor_is_a_superset_of_the_shared_baseline():
    assert capabilities_for_flavor(FLAVOR_NUKE) < capabilities_for_flavor(FLAVOR_NUKE_STUDIO)
    assert capabilities_for_flavor(FLAVOR_NUKE_X) == capabilities_for_flavor(FLAVOR_NUKE)
    assert set(STUDIO_CAPABILITIES) <= capabilities_for_flavor(FLAVOR_NUKE_STUDIO)


@pytest.mark.parametrize("flavor", [FLAVOR_NUKE, FLAVOR_NUKE_X])
def test_non_studio_flavors_lack_studio_capabilities(flavor):
    assert not supports_capability(flavor, CAPABILITY_STUDIO_TIMELINE)


def test_studio_flavor_advertises_studio_capabilities():
    assert supports_capability(FLAVOR_NUKE_STUDIO, CAPABILITY_STUDIO_TIMELINE)


def test_unknown_flavor_degrades_to_the_shared_baseline():
    assert capabilities_for_flavor("nuke-indie") == capabilities_for_flavor(FLAVOR_NUKE)


def test_nuke_env_studio_key_detects_studio(monkeypatch):
    monkeypatch.setattr(sys, "executable", "Nuke16.0")
    monkeypatch.delenv(ENV_FLAVOR_OVERRIDE, raising=False)

    report = detect_host_flavor(fake_nuke(studio=True, gui=True, NukeVersionString="16.0v1"))

    assert report.flavor == FLAVOR_NUKE_STUDIO
    assert report.signals == ("nuke.env:studio",)
    assert report.gui is True
    assert report.host_version == "16.0v1"


def test_nuke_env_hiero_key_detects_studio(monkeypatch):
    monkeypatch.setattr(sys, "executable", "Nuke16.0")
    monkeypatch.delenv(ENV_FLAVOR_OVERRIDE, raising=False)

    assert detect_host_flavor(fake_nuke(Hiero="1")).flavor == FLAVOR_NUKE_STUDIO


def test_studio_executable_name_detects_studio(monkeypatch):
    monkeypatch.setattr(sys, "executable", "C:/Program Files/Nuke16.0/NukeStudio16.0.exe")
    monkeypatch.delenv(ENV_FLAVOR_OVERRIDE, raising=False)

    report = detect_host_flavor(fake_nuke(gui=True))

    assert report.flavor == FLAVOR_NUKE_STUDIO
    assert report.signals == ("executable:studio",)


def test_nukex_env_key_detects_nukex(monkeypatch):
    monkeypatch.setattr(sys, "executable", "Nuke16.0")
    monkeypatch.delenv(ENV_FLAVOR_OVERRIDE, raising=False)

    report = detect_host_flavor(fake_nuke(nukex=True))

    assert report.flavor == FLAVOR_NUKE_X
    assert report.signals == ("nuke.env:nukex",)


def test_nukex_executable_name_detects_nukex(monkeypatch):
    monkeypatch.setattr(sys, "executable", "/opt/Nuke16.0/NukeX16.0")
    monkeypatch.delenv(ENV_FLAVOR_OVERRIDE, raising=False)

    assert detect_host_flavor(fake_nuke()).flavor == FLAVOR_NUKE_X


def test_studio_env_wins_over_nukex_because_studio_is_a_superset(monkeypatch):
    monkeypatch.setattr(sys, "executable", "NukeStudio16.0")
    monkeypatch.delenv(ENV_FLAVOR_OVERRIDE, raising=False)

    report = detect_host_flavor(fake_nuke(nukex=True, studio=True))

    assert report.flavor == FLAVOR_NUKE_STUDIO


def test_hiero_import_is_a_supporting_signal(monkeypatch):
    monkeypatch.setattr(sys, "executable", "Nuke16.0")
    monkeypatch.delenv(ENV_FLAVOR_OVERRIDE, raising=False)

    class FakeSpec:
        pass

    monkeypatch.setattr(
        "dcc_mcp_nuke.host_flavor.importlib.util.find_spec",
        lambda _name: FakeSpec(),
    )

    report = detect_host_flavor(fake_nuke())

    assert report.flavor == FLAVOR_NUKE_STUDIO
    assert report.hiero_importable is True
    assert report.signals == ("hiero-import",)


def test_plain_nuke_falls_back_to_the_baseline(monkeypatch):
    monkeypatch.setattr(sys, "executable", "Nuke16.0")
    monkeypatch.delenv(ENV_FLAVOR_OVERRIDE, raising=False)
    monkeypatch.setattr(
        "dcc_mcp_nuke.host_flavor.importlib.util.find_spec",
        lambda _name: None,
    )

    report = detect_host_flavor(fake_nuke(gui=True, NukeVersionString="16.0v1"))

    assert report.flavor == FLAVOR_NUKE
    assert report.signals == ("baseline",)
    assert report.gui is True


def test_false_string_env_values_do_not_count_as_a_signal(monkeypatch):
    monkeypatch.setattr(sys, "executable", "Nuke16.0")
    monkeypatch.delenv(ENV_FLAVOR_OVERRIDE, raising=False)
    monkeypatch.setattr(
        "dcc_mcp_nuke.host_flavor.importlib.util.find_spec",
        lambda _name: None,
    )

    assert detect_host_flavor(fake_nuke(studio="False", nukex="0")).flavor == FLAVOR_NUKE


def test_missing_nuke_module_still_classifies(monkeypatch):
    monkeypatch.setattr(sys, "executable", "Nuke16.0")
    monkeypatch.delitem(sys.modules, "nuke", raising=False)
    monkeypatch.delenv(ENV_FLAVOR_OVERRIDE, raising=False)
    monkeypatch.setattr(
        "dcc_mcp_nuke.host_flavor.importlib.util.find_spec",
        lambda _name: None,
    )

    report = detect_host_flavor()

    assert report.flavor == FLAVOR_NUKE
    assert report.host_version == "unknown"


def test_env_override_forces_the_flavor(monkeypatch):
    monkeypatch.setattr(sys, "executable", "Nuke16.0")
    monkeypatch.setenv(ENV_FLAVOR_OVERRIDE, "NukeStudio")

    report = detect_host_flavor(fake_nuke())

    assert report.flavor == FLAVOR_NUKE_STUDIO
    assert report.signals == ("env-override:nukestudio",)


def test_unrecognized_env_override_is_ignored_not_fatal(monkeypatch):
    monkeypatch.setattr(sys, "executable", "Nuke16.0")
    monkeypatch.setenv(ENV_FLAVOR_OVERRIDE, "nuke-indie")
    monkeypatch.setattr(
        "dcc_mcp_nuke.host_flavor.importlib.util.find_spec",
        lambda _name: None,
    )

    report = detect_host_flavor(fake_nuke())

    assert report.flavor == FLAVOR_NUKE
    assert report.signals == ("env-override-ignored:nuke-indie", "baseline")


def test_report_serializes_and_publishes_flat_metadata(monkeypatch):
    monkeypatch.setattr(sys, "executable", "NukeStudio16.0")
    monkeypatch.delenv(ENV_FLAVOR_OVERRIDE, raising=False)

    metadata = detect_host_flavor(fake_nuke(studio=True, gui=True)).to_instance_metadata()

    assert metadata["host_flavor"] == FLAVOR_NUKE_STUDIO
    assert metadata["host_flavor_gui"] == "true"
    assert CAPABILITY_STUDIO_TIMELINE in metadata["host_flavor_capabilities"]
    assert all(isinstance(value, str) for value in metadata.values())


def test_normalize_host_flavors_drops_unknown_entries():
    assert normalize_host_flavors(["nukestudio", "nuke-indie", " NUKE "]) == (FLAVOR_NUKE_STUDIO, FLAVOR_NUKE)
    assert normalize_host_flavors("nukex,nukestudio") == (FLAVOR_NUKE_X, FLAVOR_NUKE_STUDIO)
    assert normalize_host_flavors(None) == ()


def test_skill_host_flavors_reads_the_dcc_mcp_metadata_key():
    studio_skill = SimpleNamespace(metadata={"dcc-mcp.host-flavors": ["nukestudio"]})
    plain_skill = SimpleNamespace(metadata={"dcc-mcp.layer": "domain"})

    assert skill_host_flavors(studio_skill) == (FLAVOR_NUKE_STUDIO,)
    assert skill_host_flavors(plain_skill) == ()


def test_undeclared_skills_load_on_every_flavor():
    skill = SimpleNamespace(metadata={"dcc-mcp.layer": "domain"}, name="nuke-script")

    assert all(skill_allowed_on_flavor(skill, flavor) for flavor in HOST_FLAVORS)


def test_declared_skills_load_only_on_their_flavors():
    skill = SimpleNamespace(metadata={"dcc-mcp.host-flavors": ["nukestudio"]}, name="nuke-studio-timeline")

    assert skill_allowed_on_flavor(skill, FLAVOR_NUKE_STUDIO)
    assert not skill_allowed_on_flavor(skill, FLAVOR_NUKE)
    assert not skill_allowed_on_flavor(skill, FLAVOR_NUKE_X)


def _veto_find_spec(_name):
    return None


def _gate(monkeypatch, flavor):
    monkeypatch.setattr(sys, "executable", "Nuke16.0")
    monkeypatch.setenv(ENV_FLAVOR_OVERRIDE, flavor)
    monkeypatch.setattr("dcc_mcp_nuke.host_flavor.importlib.util.find_spec", _veto_find_spec)
    return HostFlavorGate()


def test_gate_vetoes_studio_only_skills_on_plain_nuke(monkeypatch):
    gate = _gate(monkeypatch, FLAVOR_NUKE)
    skill = SimpleNamespace(metadata={"dcc-mcp.host-flavors": ["nukestudio"]}, name="nuke-studio-timeline")

    with pytest.raises(PermissionError) as excinfo:
        gate(skill)

    assert "nuke-studio-timeline" in str(excinfo.value)
    assert "nukestudio" in str(excinfo.value)


def test_gate_allows_studio_only_skills_on_studio(monkeypatch):
    gate = _gate(monkeypatch, FLAVOR_NUKE_STUDIO)
    skill = SimpleNamespace(metadata={"dcc-mcp.host-flavors": ["nukestudio"]}, name="nuke-studio-timeline")

    assert gate(skill) is None
    assert gate.allows(skill)


def test_gate_allows_undeclared_skills_on_every_flavor(monkeypatch):
    for flavor in HOST_FLAVORS:
        gate = _gate(monkeypatch, flavor)
        skill = SimpleNamespace(metadata={}, name="nuke-script")

        assert gate(skill) is None


def test_missing_capability_error_is_explicit_and_machine_readable():
    result = missing_capability_error(
        tool_name="inspect_timeline",
        flavor=FLAVOR_NUKE,
        required=[FLAVOR_NUKE_STUDIO],
        skill_name="nuke-studio-timeline",
    )

    assert result["success"] is False
    assert result["error"] == "capability_unavailable"
    assert result["context"]["host_flavor"] == FLAVOR_NUKE
    assert result["context"]["required_host_flavors"] == [FLAVOR_NUKE_STUDIO]
    assert result["context"]["required_capabilities"]
    assert result["context"]["possible_solutions"]
    assert "Nuke Studio" in result["message"]


def test_bundled_studio_timeline_skill_declares_the_studio_flavor():
    import yaml

    skill_md = (SKILLS_ROOT / "nuke-studio-timeline" / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(skill_md.split("---")[1])
    metadata = frontmatter["metadata"]["dcc-mcp"]

    assert metadata["host-flavors"] == [FLAVOR_NUKE_STUDIO]
    assert metadata["dcc"] == FLAVOR_NUKE
