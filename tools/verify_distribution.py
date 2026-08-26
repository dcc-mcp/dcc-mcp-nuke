"""Verify built archive contents and a minimum-Core installed-wheel plan."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from email.parser import BytesParser
from email.policy import default
from pathlib import Path, PurePosixPath
from typing import Optional, Sequence

from jsonschema import Draft202012Validator
from packaging.requirements import Requirement


class DistributionContractError(RuntimeError):
    """Raised when built or installed package evidence violates the contract."""


def _safe_names(names: Sequence[str]) -> set[str]:
    normalized: set[str] = set()
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise DistributionContractError(f"unsafe archive path: {name}")
        normalized.add(name)
    return normalized


def _single_distributions(dist_dir: Path) -> tuple[Path, Path]:
    files = sorted(path for path in dist_dir.iterdir() if path.is_file()) if dist_dir.is_dir() else []
    wheels = [path for path in files if path.suffix == ".whl"]
    sdists = [path for path in files if path.name.endswith(".tar.gz")]
    if len(files) != 2 or len(wheels) != 1 or len(sdists) != 1:
        raise DistributionContractError("dist must contain exactly one wheel and one source archive")
    return wheels[0], sdists[0]


def _assert_no_compatibility_shim(names: set[str]) -> None:
    if any(PurePosixPath(name).name == "_install_contract.py" for name in names):
        raise DistributionContractError("removed compatibility shim is present in a distribution")


def inspect_distributions(dist_dir: Path, core_version: str) -> Path:
    """Inspect wheel/sdist paths and the exact minimum Core requirement."""
    wheel, sdist = _single_distributions(dist_dir)
    with zipfile.ZipFile(wheel) as archive:
        wheel_names = _safe_names(archive.namelist())
        _assert_no_compatibility_shim(wheel_names)
        if "dcc_mcp_nuke/_installer.py" not in wheel_names:
            raise DistributionContractError("wheel does not contain the Nuke lifecycle implementation")
        metadata_names = [name for name in wheel_names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise DistributionContractError("wheel must contain exactly one METADATA file")
        metadata = BytesParser(policy=default).parsebytes(archive.read(metadata_names[0]))
    requirements = [Requirement(value) for value in metadata.get_all("Requires-Dist", [])]
    core_requirements = [requirement for requirement in requirements if requirement.name == "dcc-mcp-core"]
    expected = {f">={core_version}", "<1.0.0"}
    if len(core_requirements) != 1 or {str(item) for item in core_requirements[0].specifier} != expected:
        raise DistributionContractError(f"wheel must require dcc-mcp-core>={core_version},<1.0.0")

    with tarfile.open(sdist, "r:gz") as archive:
        sdist_names = _safe_names(archive.getnames())
    _assert_no_compatibility_shim(sdist_names)
    if not any(name.endswith("/src/dcc_mcp_nuke/_installer.py") for name in sdist_names):
        raise DistributionContractError("source archive does not contain the Nuke lifecycle implementation")
    if not any(name.endswith("/pyproject.toml") for name in sdist_names):
        raise DistributionContractError("source archive does not contain pyproject.toml")
    return wheel


def _venv_python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def installed_wheel_smoke(wheel: Path, core_version: str) -> None:
    """Install the wheel with the released Core floor and validate a synthetic plan."""
    from dcc_mcp_core.deployment import load_install_sop_schema

    with tempfile.TemporaryDirectory(prefix="dcc-mcp-nuke-wheel-") as temporary:
        root = Path(temporary)
        environment = root / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = _venv_python(environment)
        for arguments in (
            ["-m", "pip", "install", "--disable-pip-version-check", f"dcc-mcp-core=={core_version}"],
            ["-m", "pip", "install", "--disable-pip-version-check", "--no-deps", str(wheel.resolve())],
        ):
            completed = subprocess.run(
                [str(python), *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()
                raise DistributionContractError(f"isolated install failed: {detail}")

        version = subprocess.run(
            [str(python), "-c", "import dcc_mcp_core; print(dcc_mcp_core.__version__)"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        if version != core_version:
            raise DistributionContractError(f"isolated environment loaded Core {version}, expected {core_version}")

        host_dir = root / "Nuke16.0v9"
        python_version = subprocess.run(
            [str(python), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        (host_dir / "lib" / f"python{python_version}").mkdir(parents=True)
        host = host_dir / ("Nuke16.0.exe" if os.name == "nt" else "Nuke16.0")
        host.write_text("synthetic host for a non-mutating install plan", encoding="utf-8")
        profile = root / "profile"
        process_env = os.environ.copy()
        process_env.update(
            {
                "DCC_MCP_NUKE_PROFILE": str(profile),
                "HOME": str(profile),
                "USERPROFILE": str(profile),
            }
        )
        completed = subprocess.run(
            [
                str(python),
                "-m",
                "dcc_mcp_nuke.install_cli",
                "install",
                "--json",
                "--dcc-path",
                str(host),
                "--python",
                str(python),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env=process_env,
        )
        if completed.returncode != 0:
            raise DistributionContractError(
                f"installed-wheel lifecycle plan failed: {(completed.stderr or completed.stdout).strip()}"
            )
        try:
            result = json.loads(completed.stdout)
        except ValueError as exc:
            raise DistributionContractError("installed-wheel lifecycle output is not JSON") from exc
        validator = Draft202012Validator(load_install_sop_schema())
        validator.validate(result)
        if result.get("status") != "planned" or result.get("core_version") != core_version:
            raise DistributionContractError("installed wheel did not use the released Core floor for its plan")
        if profile.exists():
            raise DistributionContractError("the default lifecycle plan mutated the synthetic Nuke profile")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path)
    parser.add_argument("--core-version", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        wheel = inspect_distributions(args.dist, args.core_version)
        installed_wheel_smoke(wheel, args.core_version)
    except (
        DistributionContractError,
        OSError,
        subprocess.SubprocessError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"distribution contract check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
