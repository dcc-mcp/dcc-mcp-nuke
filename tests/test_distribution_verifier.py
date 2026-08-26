from __future__ import annotations

import importlib.util
import io
import sys
import tarfile
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
