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
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


def _core_upper_bound() -> str:
    """The Core upper bound this adapter ships, read from the single source of truth."""
    import re as _re

    source = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = _re.search(r'"dcc-mcp-core>=[0-9][0-9A-Za-z.]*,<(?P<upper>[0-9][0-9A-Za-z.]*)"', source)
    assert match is not None
    return match.group("upper")


def _installer():
    from dcc_mcp_nuke import _installer

    return _installer


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
    from dcc_mcp_core.deployment import load_install_sop_schema

    validator = Draft202012Validator(load_install_sop_schema())
    validator.check_schema(validator.schema)
    validator.validate(result)
    assert result["schema_version"] == _installer().report_schema_version()
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
    assert "dcc-mcp-core>=0.20.14,<%s" % _core_upper_bound() in pyproject


def test_install_contract_uses_only_the_official_core_deployment_schema() -> None:
    root = Path(__file__).resolve().parents[1]
    package = root / "src" / "dcc_mcp_nuke"
    installer = package.joinpath("_installer.py").read_text(encoding="utf-8")

    assert not package.joinpath("_install_contract.py").exists()
    assert "from dcc_mcp_core.deployment import (" in installer
    assert "load_install_sop_schema" in installer
    assert "_validate_core_install_contract()" in installer


@pytest.mark.parametrize("schema_failure", ["missing", "malformed"])
def test_install_contract_fails_closed_when_the_official_core_schema_is_unavailable(
    tmp_path: Path,
    schema_failure: str,
) -> None:
    fake_core = tmp_path / "dcc_mcp_core"
    deployment = fake_core / "deployment"
    deployment.mkdir(parents=True)
    fake_core.joinpath("__init__.py").write_text(
        "__version__ = '0.20.14'\n"
        "inspect_install_root = probe_sidecar_tool = query_runtime_state = lambda *a, **k: {}\n"
        "safe_remove_tree = safe_replace_tree = lambda *a, **k: {'success': True}\n",
        encoding="utf-8",
    )
    if schema_failure == "missing":
        deployment.joinpath("__init__.py").write_text(
            "raise ImportError('official schema missing')\n", encoding="utf-8"
        )
        expected = "official schema missing"
    else:
        deployment.joinpath("__init__.py").write_text(
            "INSTALL_EXIT_INSTALL = 30\n"
            "INSTALL_EXIT_OK = 0\n"
            "INSTALL_EXIT_PREFLIGHT = 10\n"
            "INSTALL_EXIT_REQUIRES_RESTART = 50\n"
            "INSTALL_EXIT_VERIFY = 40\n"
            "INSTALL_SOP_SCHEMA_VERSION = 1\n"
            "def load_install_sop_schema(): return {'type': 'array'}\n",
            encoding="utf-8",
        )
        expected = "official Core Install SOP schema"

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(tmp_path), str(Path(__file__).resolve().parents[1] / "src")))
    package_root = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_nuke"
    import_script = (
        "import sys,types; "
        "package=types.ModuleType('dcc_mcp_nuke'); "
        f"package.__path__=[{str(package_root)!r}]; "
        "sys.modules['dcc_mcp_nuke']=package; "
        "import dcc_mcp_nuke._installer"
    )
    completed = subprocess.run(
        [sys.executable, "-c", import_script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode != 0
    assert expected in completed.stderr


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
    # The runbook advertises the Core range users must install, so it has to agree with the
    # pin that actually guards them. A stale ``<1.0.0`` here tells users a Core minor is
    # supported right up to the point pip refuses to install it.
    assert f"`>=0.20.14,<{_core_upper_bound()}`" in runbook


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


def test_core_install_contract_accepts_a_revision_bump_of_the_published_artifact() -> None:
    """The import-time contract must not equate the report const with the artifact revision.

    Core 0.20.34 publishes the ``-v2`` schema artifact while the report's ``schema_version``
    const stays at 1, because v2 only adds the optional ``catalog`` object. Equating the two
    made this module raise at import on 0.20.34 -- the adapter was unusable, not merely
    emitting a rejected report.
    """
    installer = _installer()
    schema = installer._validate_core_install_contract()

    declared = schema["properties"]["schema_version"]["const"]
    assert declared == installer.report_schema_version()
    assert declared == 1


def test_report_schema_version_ignores_cores_artifact_revision() -> None:
    """The report field follows the published document's const, not Core's exported constant."""
    installer = _installer()

    assert installer.report_schema_version() == 1


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("Install SOP schema integrity error: schema_digest_mismatch"),
        OSError("schema file unreadable"),
        ValueError("schema document is not valid JSON"),
    ],
)
def test_report_schema_version_falls_back_when_the_document_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """An unhealthy Core must not stop the CLI from emitting a report.

    Core signals schema_unavailable / schema_digest_mismatch with RuntimeError, unreadable
    files with OSError, and a corrupt document with ValueError. All three are reachable and all
    three must degrade, so the read failure is parametrized rather than pinned to one shape.
    """
    installer = _installer()
    monkeypatch.setattr(installer, "load_install_sop_schema", _raise(error))

    assert installer.report_schema_version() == installer.FALLBACK_REPORT_SCHEMA_VERSION


def _raise(error: Exception):
    def _raiser():
        raise error

    return _raiser


def test_core_dependency_stays_pinned_below_the_next_minor() -> None:
    """``<1.0.0`` admits any future Core minor, which is how 0.20.34 shipped unannounced."""
    assert _core_upper_bound() == "0.21.0"


def test_ci_core_latest_job_resolves_a_real_core_version() -> None:
    """The early-warning job must fail loudly rather than test an empty pin."""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["core-latest"]
    resolve = [step for step in job["steps"] if step.get("id") == "core"]
    assert resolve, "core-latest job has no version resolution step"

    script = resolve[0]["run"]
    assert "exit 1" in script, "empty version resolution must fail the job"
    assert "::error::" in script


@pytest.mark.parametrize(
    "document",
    [
        {"type": "object", "properties": {"schema_version": "not-an-object"}},
        {"type": "object", "properties": {"schema_version": {"const": "1"}}},
        {"type": "object", "properties": {"schema_version": {"const": True}}},
        {"type": "object", "properties": "not-an-object"},
        {"type": "object"},
    ],
)
def test_malformed_schema_document_degrades_to_the_fallback(monkeypatch, document):
    """A wrong-shaped schema must not raise above the caller's own except.

    Core at this adapter's floor loads the schema with a bare ``json.loads`` -- no type and no
    digest validation -- so valid JSON of the wrong shape is reachable. Reading the const with a
    chained lookup raises AttributeError from a place the caller does not guard, which at import
    time takes the whole adapter down rather than degrading to the fallback.
    """
    installer = _installer()
    monkeypatch.setattr(installer, "load_install_sop_schema", lambda: document)

    assert installer.report_schema_version() == installer.FALLBACK_REPORT_SCHEMA_VERSION


@pytest.mark.parametrize(
    "document",
    [
        {"type": "object", "properties": {"schema_version": "not-an-object"}},
        {"type": "object", "properties": "not-an-object"},
        {"type": "object"},
    ],
)
def test_malformed_schema_document_fails_the_import_contract(monkeypatch, document):
    """The import-time contract must reject a wrong-shaped document, not raise AttributeError."""
    installer = _installer()
    monkeypatch.setattr(installer, "load_install_sop_schema", lambda: document)

    with pytest.raises(RuntimeError, match="unavailable or incompatible"):
        installer._validate_core_install_contract()


def test_chained_lookup_really_does_raise_on_this_document():
    """Teeth: the malformed cases above must break the shape this fix replaced.

    An assertion like "the new code returns the fallback" is true for almost any implementation,
    including one that never had the bug. This pins the mechanism instead: on these documents the
    previous chained lookup raises AttributeError, and the guarded walk returns None. Without the
    first half, the other tests in this group would pass against a no-op and prove nothing.
    """
    installer = _installer()
    malformed = {"type": "object", "properties": []}

    with pytest.raises(AttributeError):
        # Exactly what _validate_core_install_contract() used to do.
        malformed.get("properties", {}).get("schema_version", {}).get("const")

    assert installer._schema_const(malformed) is None
