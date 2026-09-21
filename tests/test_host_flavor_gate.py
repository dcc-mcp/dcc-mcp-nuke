from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dcc_mcp_nuke.dispatcher import NukeDispatcher
from dcc_mcp_nuke.host_flavor import ENV_FLAVOR_OVERRIDE, FLAVOR_NUKE, FLAVOR_NUKE_STUDIO

SKILLS_ROOT = Path(__file__).parents[1] / "src" / "dcc_mcp_nuke" / "skills"
STUDIO_SKILL = "nuke-studio-timeline"


@pytest.fixture(autouse=True)
def _no_qt_timer(monkeypatch):
    """Keep the unit tests off Nuke's Qt event loop."""
    monkeypatch.setattr(NukeDispatcher, "start", lambda _self: None)
    monkeypatch.setattr(NukeDispatcher, "stop", lambda _self: None)
    monkeypatch.setattr(sys, "executable", "Nuke16.0")
    monkeypatch.setattr("dcc_mcp_nuke.host_flavor.importlib.util.find_spec", lambda _name: None)
    yield


def _server(monkeypatch, flavor):
    monkeypatch.setenv(ENV_FLAVOR_OVERRIDE, flavor)
    from dcc_mcp_nuke.server import NukeMcpServer

    server = NukeMcpServer(port=0)
    server.register_builtin_actions()
    return server


def test_server_reports_the_detected_host_flavor(monkeypatch):
    server = _server(monkeypatch, FLAVOR_NUKE_STUDIO)
    try:
        assert server.host_flavor == FLAVOR_NUKE_STUDIO
        assert server.host_flavor_report.flavor == FLAVOR_NUKE_STUDIO
    finally:
        server.stop()


def test_server_publishes_host_flavor_as_instance_metadata(monkeypatch):
    server = _server(monkeypatch, FLAVOR_NUKE_STUDIO)
    try:
        metadata = server._config.instance_metadata

        assert metadata["host_flavor"] == FLAVOR_NUKE_STUDIO
        assert "studio.timeline" in metadata["host_flavor_capabilities"]
    finally:
        server.stop()


def test_studio_only_skill_is_discovered_but_refused_on_plain_nuke(monkeypatch):
    server = _server(monkeypatch, FLAVOR_NUKE)
    try:
        names = [skill["name"] if isinstance(skill, dict) else skill.name for skill in server.list_skills()]

        assert STUDIO_SKILL in names, "the Studio skill stays discoverable so agents see the capability exists"

        with pytest.raises(Exception) as excinfo:
            server._server.load_skill(STUDIO_SKILL)

        assert STUDIO_SKILL in str(excinfo.value)
        assert FLAVOR_NUKE in str(excinfo.value)
        assert [
            action["name"] for action in server.list_actions() if STUDIO_SKILL in str(action.get("skill", ""))
        ] == []
    finally:
        server.stop()


def test_studio_only_skill_loads_on_studio(monkeypatch):
    server = _server(monkeypatch, FLAVOR_NUKE_STUDIO)
    try:
        assert server.load_skill(STUDIO_SKILL) is True

        actions = [action["name"] for action in server.list_actions()]
        assert "nuke_studio_timeline__inspect_timeline" in actions
    finally:
        server.stop()


def test_shared_skills_still_load_on_plain_nuke(monkeypatch):
    server = _server(monkeypatch, FLAVOR_NUKE)
    try:
        assert server.load_skill("nuke-diagnostics") is True
        assert server.load_skill("nuke-script") is True
    finally:
        server.stop()


def test_bundled_skills_declare_only_known_host_flavors():
    from tools.skill_companion_audit import audit_host_flavor_metadata

    assert audit_host_flavor_metadata(SKILLS_ROOT) == []


def test_host_flavor_audit_rejects_an_unknown_flavor(tmp_path):
    from tools.skill_companion_audit import audit_host_flavor_metadata

    skill_root = tmp_path / "nuke-future-host"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text(
        "---\n"
        "name: nuke-future-host\n"
        "description: Declares a flavor that does not exist.\n"
        "metadata:\n"
        "  dcc-mcp:\n"
        "    dcc: nuke\n"
        "    host-flavors: [nuke-indie]\n"
        "---\n"
        "\n"
        "# Body\n",
        encoding="utf-8",
    )

    issues = audit_host_flavor_metadata(tmp_path)

    assert len(issues) == 1
    assert "nuke-future-host" in issues[0]
    assert "nuke-indie" in issues[0]


def test_host_flavor_audit_rejects_a_mixed_declaration(tmp_path):
    """A mixed declaration must fail even though normalizing it yields a valid gate.

    ``normalize_host_flavors`` drops unknown entries, so ``nuke, nuke-indie``
    normalizes to ``("nuke",)``. Auditing only the normalized result would let the
    typo through and ship a manifest that silently disagrees with its intent.
    """
    from tools.skill_companion_audit import audit_host_flavor_metadata

    skill_root = tmp_path / "nuke-mixed-host"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text(
        """---
name: nuke-mixed-host
description: Declares one known and one unknown flavor.
metadata:
  dcc-mcp:
    dcc: nuke
    host-flavors: "nuke, nuke-indie"
---

# Body
""",
        encoding="utf-8",
    )

    issues = audit_host_flavor_metadata(tmp_path)

    assert len(issues) == 1
    assert "nuke-mixed-host" in issues[0]
    assert "nuke-indie" in issues[0]
