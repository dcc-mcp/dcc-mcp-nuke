from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "dcc_mcp_nuke"
    / "skills"
    / "nuke-studio-timeline"
    / "scripts"
    / "inspect_timeline.py"
)
MODULE = "dcc_mcp_nuke.host_flavor"


@pytest.fixture(autouse=True)
def _clean_nuke_module():
    sys.modules.pop("nuke", None)
    sys.modules.pop("hiero", None)
    sys.modules.pop("hiero.core", None)
    yield
    sys.modules.pop("nuke", None)
    sys.modules.pop("hiero", None)
    sys.modules.pop("hiero.core", None)


def _load(monkeypatch, flavor, executable="NukeStudio16.0", find_spec=None):
    monkeypatch.setenv("DCC_MCP_NUKE_HOST_FLAVOR", flavor)
    monkeypatch.setattr(sys, "executable", executable)
    if find_spec is None:
        find_spec = lambda _name: None  # noqa: E731
    monkeypatch.setattr(f"{MODULE}.importlib.util.find_spec", find_spec)
    return runpy.run_path(str(SCRIPT))


def test_non_studio_flavor_returns_an_explicit_capability_error(monkeypatch):
    namespace = _load(monkeypatch, "nuke", executable="Nuke16.0")

    result = namespace["main"]()

    assert result["success"] is False
    assert result["error"] == "capability_unavailable"
    assert result["context"]["host_flavor"] == "nuke"
    assert result["context"]["required_host_flavors"] == ["nukestudio"]
    assert result["context"]["possible_solutions"]


def test_nukex_flavor_is_also_refused(monkeypatch):
    namespace = _load(monkeypatch, "nukex", executable="NukeX16.0")

    result = namespace["main"]()

    assert result["success"] is False
    assert result["error"] == "capability_unavailable"
    assert result["context"]["host_flavor"] == "nukex"


def test_max_sequences_is_bounded_before_the_flavor_gate(monkeypatch):
    namespace = _load(monkeypatch, "nukestudio")

    result = namespace["main"](max_sequences=0)

    assert result["success"] is False
    assert result["error"] == "invalid_range"


def test_studio_flavor_without_hiero_reports_the_missing_surface(monkeypatch):
    namespace = _load(monkeypatch, "nukestudio")

    result = namespace["main"]()

    assert result["success"] is False
    assert result["error"] == "studio_surface_unavailable"
    assert result["context"]["host_flavor"] == "nukestudio"
    assert result["context"]["studio_surface"] is False
    assert "hiero" in result["context"]["reason"]


def test_studio_flavor_reports_the_hiero_timeline_surface(monkeypatch):
    sequence = SimpleNamespace(
        name=lambda: "SH010",
        inTime=lambda: 1001,
        outTime=lambda: 1100,
        videoTracks=lambda: ["V1", "V2"],
        audioTracks=lambda: ["A1"],
    )
    project = SimpleNamespace(name=lambda: "Demo", sequences=lambda: [sequence])
    hiero_core = SimpleNamespace(projects=lambda: [project])

    class FakeSpec:
        pass

    monkeypatch.setitem(sys.modules, "hiero", SimpleNamespace(core=hiero_core))
    monkeypatch.setitem(sys.modules, "hiero.core", hiero_core)
    namespace = _load(monkeypatch, "nukestudio", find_spec=lambda _name: FakeSpec())

    result = namespace["main"]()

    assert result["success"] is True
    assert result["context"]["host_flavor"] == "nukestudio"
    assert result["context"]["studio_surface"] is True
    assert result["context"]["project_count"] == 1
    reported = result["context"]["projects"][0]
    assert reported["name"] == "Demo"
    assert reported["sequences"][0] == {
        "name": "SH010",
        "first_frame": 1001,
        "last_frame": 1100,
        "video_track_count": 2,
        "audio_track_count": 1,
    }
    assert result["context"]["reason"] == ""


def test_studio_flavor_reports_a_missing_sequence_api_explicitly(monkeypatch):
    project = SimpleNamespace(name=lambda: "Demo")
    hiero_core = SimpleNamespace(projects=lambda: [project])

    class FakeSpec:
        pass

    monkeypatch.setitem(sys.modules, "hiero", SimpleNamespace(core=hiero_core))
    monkeypatch.setitem(sys.modules, "hiero.core", hiero_core)
    namespace = _load(monkeypatch, "nukestudio", find_spec=lambda _name: FakeSpec())

    result = namespace["main"]()

    assert result["success"] is True
    assert "sequences()" in result["context"]["reason"]


def test_studio_flavor_reports_a_failing_projects_call(monkeypatch):
    def _boom():
        raise RuntimeError("hiero is not ready")

    hiero_core = SimpleNamespace(projects=_boom)

    class FakeSpec:
        pass

    monkeypatch.setitem(sys.modules, "hiero", SimpleNamespace(core=hiero_core))
    monkeypatch.setitem(sys.modules, "hiero.core", hiero_core)
    namespace = _load(monkeypatch, "nukestudio", find_spec=lambda _name: FakeSpec())

    result = namespace["main"]()

    assert result["success"] is False
    assert result["error"] == "studio_surface_unavailable"
    assert "RuntimeError" in result["context"]["reason"]
