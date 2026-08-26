from __future__ import annotations

import builtins
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
import types
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "tools" / "verify_distribution.py"


def _verifier_module():
    assert VERIFIER.is_file(), "built distributions need a source-controlled minimum-Core verifier"
    spec = importlib.util.spec_from_file_location("verify_distribution", VERIFIER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _add_tar_file(archive: tarfile.TarFile, name: str, content: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    archive.addfile(info, io.BytesIO(content))


def test_distribution_verifier_rejects_the_removed_compatibility_shim(tmp_path: Path) -> None:
    verifier = _verifier_module()
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "dcc_mcp_nuke-0.14.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("dcc_mcp_nuke/_installer.py", "official deployment import")
        archive.writestr("dcc_mcp_nuke/_install_contract.py", "fallback")
        archive.writestr(
            "dcc_mcp_nuke-0.14.0.dist-info/METADATA",
            "Name: dcc-mcp-nuke\nVersion: 0.14.0\nRequires-Dist: dcc-mcp-core<1.0.0,>=0.20.14\n",
        )
    sdist = dist / "dcc_mcp_nuke-0.14.0.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        _add_tar_file(archive, "dcc_mcp_nuke-0.14.0/src/dcc_mcp_nuke/_installer.py", b"official")
        _add_tar_file(archive, "dcc_mcp_nuke-0.14.0/src/dcc_mcp_nuke/_install_contract.py", b"fallback")

    with pytest.raises(verifier.DistributionContractError, match="compatibility shim"):
        verifier.inspect_distributions(dist, "0.20.14")


def test_ci_runs_the_minimum_core_installed_wheel_contract_smoke() -> None:
    workflow = ROOT.joinpath(".github", "workflows", "ci.yml").read_text(encoding="utf-8")

    assert "Verify wheel and sdist with minimum Core" in workflow
    assert "python tools/verify_distribution.py dist --core-version 0.20.14" in workflow


def _valid_core_probe(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    payload = {
        "core_version": "0.20.14",
        "install_sop_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
        },
    }
    return subprocess.CompletedProcess(arguments, 0, json.dumps(payload), "")


def test_core_schema_probe_uses_the_isolated_python_when_ambient_core_is_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    verifier = _verifier_module()
    isolated_python = tmp_path / "isolated-python"
    real_import = builtins.__import__

    def reject_ambient_core(name, *args, **kwargs):
        if name == "dcc_mcp_core" or name.startswith("dcc_mcp_core."):
            raise ModuleNotFoundError("ambient Core is intentionally absent")
        return real_import(name, *args, **kwargs)

    def run(arguments, **kwargs):
        assert Path(arguments[0]) == isolated_python
        assert arguments[1] == "-c"
        return _valid_core_probe(arguments)

    monkeypatch.setattr(builtins, "__import__", reject_ambient_core)
    monkeypatch.setattr(verifier.subprocess, "run", run)

    schema = verifier._load_isolated_core_contract(isolated_python, "0.20.14")

    assert schema["type"] == "object"


def test_core_schema_probe_ignores_a_different_ambient_core(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    verifier = _verifier_module()
    ambient_core = types.ModuleType("dcc_mcp_core")
    ambient_core.__version__ = "9.9.9"
    ambient_deployment = types.ModuleType("dcc_mcp_core.deployment")
    ambient_deployment.load_install_sop_schema = lambda: {"type": 42}
    monkeypatch.setitem(sys.modules, "dcc_mcp_core", ambient_core)
    monkeypatch.setitem(sys.modules, "dcc_mcp_core.deployment", ambient_deployment)
    monkeypatch.setattr(verifier.subprocess, "run", lambda arguments, **kwargs: _valid_core_probe(arguments))

    schema = verifier._load_isolated_core_contract(tmp_path / "isolated-python", "0.20.14")

    assert schema["type"] == "object"


@pytest.mark.parametrize(
    ("completed", "message"),
    [
        (subprocess.CompletedProcess([], 1, "", "missing deployment module"), "probe failed"),
        (subprocess.CompletedProcess([], 0, "{", ""), "malformed JSON"),
        (
            subprocess.CompletedProcess([], 0, json.dumps({"core_version": "0.20.14"}), ""),
            "invalid fields",
        ),
        (
            subprocess.CompletedProcess(
                [],
                0,
                json.dumps({"core_version": "0.20.20", "install_sop_schema": {"type": "object"}}),
                "",
            ),
            "loaded Core 0.20.20",
        ),
        (
            subprocess.CompletedProcess(
                [],
                0,
                json.dumps({"core_version": "0.20.14", "install_sop_schema": {"type": 42}}),
                "",
            ),
            "schema is invalid",
        ),
    ],
)
def test_core_schema_probe_wraps_import_version_and_schema_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    completed: subprocess.CompletedProcess[str],
    message: str,
) -> None:
    verifier = _verifier_module()
    monkeypatch.setattr(verifier.subprocess, "run", lambda arguments, **kwargs: completed)

    with pytest.raises(verifier.DistributionContractError, match=message):
        verifier._load_isolated_core_contract(tmp_path / "isolated-python", "0.20.14")
