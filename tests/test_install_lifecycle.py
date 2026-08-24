from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml


def test_install_defaults_to_a_non_mutating_agent_plan(tmp_path: Path) -> None:
    host_dir = tmp_path / "Nuke16.0v9"
    host_dir.mkdir()
    (host_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}").mkdir(parents=True)
    host = host_dir / ("Nuke16.0.exe" if os.name == "nt" else "Nuke16.0")
    host.write_text("synthetic host", encoding="utf-8")

    profile = tmp_path / "profile"
    env = os.environ.copy()
    env.update({"HOME": str(profile), "USERPROFILE": str(profile)})

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "dcc_mcp_nuke.install_cli",
            "install",
            "--json",
            "--dcc-path",
            str(host),
            "--python",
            sys.executable,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["schema_version"] == 1
    assert result["status"] == "planned"
    assert result["dcc_type"] == "nuke"
    assert result["plan"]["host_version"] == "16.0v9"
    assert Path(result["plan"]["python"]["executable"]).resolve() == Path(sys.executable).resolve()
    assert result["next_steps"] == [
        {
            "id": "execute",
            "description": "Execute the validated Nuke install plan.",
            "command": [
                "dcc-mcp-nuke",
                "install",
                "--json",
                "--yes",
                "--dcc-path",
                str(host.resolve()),
                "--python",
                str(Path(sys.executable).resolve()),
            ],
            "why": "Planning does not modify Nuke or the install receipt.",
        }
    ]
    assert not profile.exists()


def test_install_receipt_round_trip_preserves_shared_profile_init(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    host_dir = tmp_path / "Nuke16.0v9"
    host_dir.mkdir()
    host = host_dir / ("Nuke16.0.exe" if os.name == "nt" else "Nuke16.0")
    host.write_text("synthetic host", encoding="utf-8")
    profile = tmp_path / "profile"
    profile.mkdir()
    shared_init = profile / "init.py"
    shared_init.write_text("# user-owned Nuke startup\n", encoding="utf-8")
    monkeypatch.setenv("DCC_MCP_NUKE_PROFILE", str(profile))
    monkeypatch.setenv("DCC_MCP_REGISTRY_DIR", str(tmp_path / "registry"))
    monkeypatch.setenv("DCC_MCP_INSTALL_VERIFY_TIMEOUT", "0.01")

    (host_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}").mkdir(parents=True)

    from dcc_mcp_nuke import install_cli

    common = ["--json", "--dcc-path", str(host), "--python", sys.executable]

    assert install_cli.main(["install", *common, "--yes"]) == 40
    installed = json.loads(capsys.readouterr().out)
    assert installed["status"] == "partial"
    assert installed["verify"]["failure_stage"] == "readiness"
    receipt_path = Path(installed["receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    owned = {Path(item["path"]) for item in receipt["files"]}
    assert owned == {
        profile / "dcc-mcp-nuke" / "init.py",
        profile / "dcc-mcp-nuke" / "menu.py",
    }
    assert all(path.is_file() for path in owned)
    assert all(len(item["sha256"]) == 64 for item in receipt["files"])
    assert "# user-owned Nuke startup" in shared_init.read_text(encoding="utf-8")
    assert "DCC-MCP NUKE MANAGED START" in shared_init.read_text(encoding="utf-8")

    assert install_cli.main(["uninstall", *common]) == 0
    capsys.readouterr()
    assert receipt_path.is_file()

    assert install_cli.main(["uninstall", *common, "--yes"]) == 0
    removed = json.loads(capsys.readouterr().out)
    assert removed["status"] == "ok"
    assert not receipt_path.exists()
    assert all(not path.exists() for path in owned)
    assert shared_init.read_text(encoding="utf-8") == "# user-owned Nuke startup\n"

    assert install_cli.main(["uninstall", *common, "--yes"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_packaged_nuke_startup_captures_import_and_startup_errors() -> None:
    startup = (Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_nuke" / "nuke_plugin" / "init.py").read_text(
        encoding="utf-8"
    )

    assert "capture_bootstrap_errors" in startup
    assert 'phase="import"' in startup
    assert 'phase="startup"' in startup


def test_distribution_exposes_the_standard_lifecycle_entry_point() -> None:
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")

    assert "[project.scripts]" in pyproject
    assert 'dcc-mcp-nuke = "dcc_mcp_nuke.install_cli:main"' in pyproject
    assert "dcc-mcp-core>=0.20.8,<1.0.0" in pyproject


def test_diagnostics_ping_is_typed_read_only_and_reports_host_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_nuke" / "skills" / "nuke-diagnostics"
    tools = yaml.safe_load((skill / "tools.yaml").read_text(encoding="utf-8"))["tools"]
    ping = next(tool for tool in tools if tool["name"] == "ping")
    assert ping["read_only"] is True
    assert ping["affinity"] == "main"

    monkeypatch.setitem(
        sys.modules,
        "nuke",
        types.SimpleNamespace(env={"gui": True, "NukeVersionString": "16.0v9"}),
    )
    namespace = runpy.run_path(str(skill / "scripts" / "ping.py"))

    assert namespace["main"]() == {
        "ready": True,
        "dcc": "nuke",
        "host_version": "16.0v9",
        "gui": True,
    }


def test_install_runbook_covers_lifecycle_platforms_and_nuke_preflight() -> None:
    runbook = (Path(__file__).resolve().parents[1] / "install.md").read_text(encoding="utf-8")

    for heading in (
        "## Requirements",
        "## Supported versions",
        "## Agent quick path",
        "## Manual path",
        "## Verify",
        "## Upgrade",
        "## Uninstall",
        "## Troubleshooting",
    ):
        assert heading in runbook
    for platform_name in ("Windows", "macOS", "Linux"):
        assert platform_name in runbook
    for verb in ("install", "status", "verify", "upgrade", "uninstall"):
        assert f"dcc-mcp-nuke {verb}" in runbook
    assert "NUKE_PATH" in runbook
    assert "Nuke 14" in runbook and "Python 3.9" in runbook
    assert "Nuke 16" in runbook and "Python 3.11" in runbook
    assert "bootstrap" in runbook.lower()


def test_ci_runs_the_install_lifecycle_smoke_explicitly() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "Install lifecycle smoke" in workflow
    assert "python -m pytest tests/test_install_lifecycle.py" in workflow


def test_ci_covers_supported_nuke_embedded_python_lines() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    versions = set(workflow["jobs"]["test"]["strategy"]["matrix"]["python"])
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")

    assert {"3.9", "3.10", "3.11", "3.13"} <= versions
    assert 'Programming Language :: Python :: 3.13"' in pyproject


def test_status_detects_and_install_repairs_a_receipted_missing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    host_dir = tmp_path / "Nuke16.0v9"
    (host_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}").mkdir(parents=True)
    host = host_dir / ("Nuke16.0.exe" if os.name == "nt" else "Nuke16.0")
    host.write_text("synthetic host", encoding="utf-8")
    profile = tmp_path / "profile"
    monkeypatch.setenv("DCC_MCP_NUKE_PROFILE", str(profile))
    monkeypatch.setenv("DCC_MCP_REGISTRY_DIR", str(tmp_path / "registry"))
    common = ["--json", "--dcc-path", str(host), "--python", sys.executable]

    from dcc_mcp_nuke.install_cli import main

    assert main(["install", *common, "--yes"]) == 40
    installed = json.loads(capsys.readouterr().out)
    receipt = json.loads(Path(installed["receipt_path"]).read_text(encoding="utf-8"))
    Path(receipt["files"][0]["path"]).unlink()

    assert main(["status", *common]) == 10
    status = json.loads(capsys.readouterr().out)
    assert status["install_state"] == "repair"

    assert main(["install", *common, "--yes"]) == 40
    capsys.readouterr()
    assert main(["status", *common]) == 0
    repaired = json.loads(capsys.readouterr().out)
    assert repaired["install_state"] == "current"


def test_failed_upgrade_restores_plugin_registration_and_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    host_dir = tmp_path / "Nuke16.0v9"
    (host_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}").mkdir(parents=True)
    host = host_dir / ("Nuke16.0.exe" if os.name == "nt" else "Nuke16.0")
    host.write_text("synthetic host", encoding="utf-8")
    profile = tmp_path / "profile"
    profile.mkdir()
    shared_init = profile / "init.py"
    shared_init.write_text("# studio startup\n", encoding="utf-8")
    monkeypatch.setenv("DCC_MCP_NUKE_PROFILE", str(profile))
    monkeypatch.setenv("DCC_MCP_REGISTRY_DIR", str(tmp_path / "registry"))
    common = ["--json", "--dcc-path", str(host), "--python", sys.executable]

    from dcc_mcp_nuke import _installer
    from dcc_mcp_nuke.install_cli import main

    assert main(["install", *common, "--yes"]) == 40
    installed = json.loads(capsys.readouterr().out)
    receipt_path = Path(installed["receipt_path"])
    receipt_before = receipt_path.read_bytes()
    shared_before = shared_init.read_bytes()
    plugin_root = profile / "dcc-mcp-nuke"
    plugin_before = {path.name: path.read_bytes() for path in plugin_root.iterdir()}

    monkeypatch.setattr(
        _installer,
        "_write_json_atomic",
        lambda _path, _payload: (_ for _ in ()).throw(OSError("injected receipt commit failure")),
    )
    assert main(["upgrade", *common, "--yes"]) == 30
    failed = json.loads(capsys.readouterr().out)

    assert failed["verify"]["failure_stage"] == "install"
    assert receipt_path.read_bytes() == receipt_before
    assert shared_init.read_bytes() == shared_before
    assert {path.name: path.read_bytes() for path in plugin_root.iterdir()} == plugin_before
    staging = profile / ".dcc-mcp" / "staging"
    assert not staging.exists() or not any(staging.iterdir())


def test_preflight_rejects_python_that_does_not_match_nukes_embedded_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    host_dir = tmp_path / "Nuke16.0v9"
    mismatched_python = "3.11" if sys.version_info[:2] == (3, 10) else "3.10"
    (host_dir / "lib" / f"python{mismatched_python}").mkdir(parents=True)
    host = host_dir / ("Nuke16.0.exe" if os.name == "nt" else "Nuke16.0")
    host.write_text("synthetic host", encoding="utf-8")
    profile = tmp_path / "profile"
    monkeypatch.setenv("DCC_MCP_NUKE_PROFILE", str(profile))

    from dcc_mcp_nuke.install_cli import main

    assert main(["install", "--json", "--dcc-path", str(host), "--python", sys.executable]) == 10
    failed = json.loads(capsys.readouterr().out)
    assert failed["verify"]["failure_stage"] == "python_compatibility"
    assert f"requires Python {mismatched_python}" in failed["verify"]["failure_reason"]
    assert not profile.exists()


def test_readiness_probe_rejects_non_loopback_registry_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dcc_mcp_nuke import _installer

    monkeypatch.setattr(
        _installer,
        "probe_sidecar_tool",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unsafe transport attempted")),
    )

    result = _installer._safe_probe("https://example.com/mcp", 0.1)

    assert result["success"] is False
    assert result["status"] == "probe_unsafe_url"


def test_readiness_probe_retries_core_406_with_streamable_http_accept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dcc_mcp_nuke import _installer

    monkeypatch.setattr(
        _installer,
        "probe_sidecar_tool",
        lambda *_args, **_kwargs: {
            "success": False,
            "status": "probe_http_error",
            "http_status": 406,
        },
    )

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args) -> bytes:
            return b'data: {"jsonrpc":"2.0","id":"nuke-install-readiness","result":{"content":[]}}\n\n'

    def open_streamable(request, *, timeout):
        assert timeout == 0.5
        assert request.headers["Accept"] == "application/json, text/event-stream"
        payload = json.loads(request.data)
        assert payload["params"] == {"name": "nuke_diagnostics__ping", "arguments": {}}
        return Response()

    monkeypatch.setattr(_installer.urllib.request, "urlopen", open_streamable)

    result = _installer._safe_probe("http://127.0.0.1:17777/mcp", 0.5)

    assert result["success"] is True
    assert result["status"] == "probe_ok"


def test_install_reports_restart_only_from_core_lock_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    host_dir = tmp_path / "Nuke16.0v9"
    (host_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}").mkdir(parents=True)
    host = host_dir / ("Nuke16.0.exe" if os.name == "nt" else "Nuke16.0")
    host.write_text("synthetic host", encoding="utf-8")
    profile = tmp_path / "profile"
    monkeypatch.setenv("DCC_MCP_NUKE_PROFILE", str(profile))

    from dcc_mcp_nuke import _installer
    from dcc_mcp_nuke.install_cli import main

    monkeypatch.setattr(
        _installer,
        "inspect_install_root",
        lambda path: {
            "success": True,
            "status": "requires_restart",
            "requires_restart": True,
            "install_root": str(path),
            "locked_path": str(Path(path) / "loaded.pyd"),
        },
    )

    assert main(["install", "--json", "--yes", "--dcc-path", str(host), "--python", sys.executable]) == 50
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "requires_restart"
    assert result["lock"]["locked_path"].endswith("loaded.pyd")
    assert not profile.exists()


def test_uninstall_preserves_a_modified_receipted_plugin_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    host_dir = tmp_path / "Nuke16.0v9"
    (host_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}").mkdir(parents=True)
    host = host_dir / ("Nuke16.0.exe" if os.name == "nt" else "Nuke16.0")
    host.write_text("synthetic host", encoding="utf-8")
    profile = tmp_path / "profile"
    monkeypatch.setenv("DCC_MCP_NUKE_PROFILE", str(profile))
    monkeypatch.setenv("DCC_MCP_REGISTRY_DIR", str(tmp_path / "registry"))
    common = ["--json", "--dcc-path", str(host), "--python", sys.executable]

    from dcc_mcp_nuke.install_cli import main

    assert main(["install", *common, "--yes"]) == 40
    installed = json.loads(capsys.readouterr().out)
    receipt_path = Path(installed["receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    modified = Path(receipt["files"][0]["path"])
    modified.write_text("# operator modification\n", encoding="utf-8")

    assert main(["uninstall", *common, "--yes"]) == 30
    failure = json.loads(capsys.readouterr().out)
    assert failure["verify"]["failure_stage"] == "receipt"
    assert modified.is_file()
    assert receipt_path.is_file()


def test_failed_uninstall_restores_the_receipted_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    host_dir = tmp_path / "Nuke16.0v9"
    (host_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}").mkdir(parents=True)
    host = host_dir / ("Nuke16.0.exe" if os.name == "nt" else "Nuke16.0")
    host.write_text("synthetic host", encoding="utf-8")
    profile = tmp_path / "profile"
    profile.mkdir()
    shared_init = profile / "init.py"
    shared_init.write_text("# studio startup\n", encoding="utf-8")
    monkeypatch.setenv("DCC_MCP_NUKE_PROFILE", str(profile))
    monkeypatch.setenv("DCC_MCP_REGISTRY_DIR", str(tmp_path / "registry"))
    common = ["--json", "--dcc-path", str(host), "--python", sys.executable]

    from dcc_mcp_nuke import _installer
    from dcc_mcp_nuke.install_cli import main

    assert main(["install", *common, "--yes"]) == 40
    installed = json.loads(capsys.readouterr().out)
    receipt_path = Path(installed["receipt_path"])
    receipt_before = receipt_path.read_bytes()
    shared_before = shared_init.read_bytes()
    plugin_root = profile / "dcc-mcp-nuke"
    plugin_before = {path.name: path.read_bytes() for path in plugin_root.iterdir()}

    monkeypatch.setattr(
        _installer,
        "_write_text_atomic",
        lambda _path, _content: (_ for _ in ()).throw(OSError("injected shared init failure")),
    )
    assert main(["uninstall", *common, "--yes"]) == 30
    capsys.readouterr()

    assert receipt_path.read_bytes() == receipt_before
    assert shared_init.read_bytes() == shared_before
    assert {path.name: path.read_bytes() for path in plugin_root.iterdir()} == plugin_before


@pytest.mark.skipif(os.name != "nt", reason="Windows standard Nuke discovery")
def test_preflight_discovers_one_standard_windows_nuke_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    host_dir = tmp_path / "Nuke16.0v9"
    (host_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}").mkdir(parents=True)
    host = host_dir / "Nuke16.0.exe"
    host.write_text("synthetic host", encoding="utf-8")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramW6432", str(tmp_path))
    monkeypatch.setenv("DCC_MCP_NUKE_PROFILE", str(tmp_path / "profile"))

    from dcc_mcp_nuke.install_cli import main

    assert main(["install", "--json", "--python", sys.executable, "--dry-run"]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["plan"]["host_path"] == str(host.resolve())
