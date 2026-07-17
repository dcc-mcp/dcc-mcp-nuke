import json
import re
from pathlib import Path

import dcc_mcp_nuke

ROOT = Path(__file__).resolve().parents[1]


def test_version_metadata_is_synchronized():
    version = re.search(r'(?m)^version = "([^"]+)"$', ROOT.joinpath("pyproject.toml").read_text())
    assert (
        version
        and version.group(1)
        == dcc_mcp_nuke.__version__
        == json.loads(ROOT.joinpath(".release-please-manifest.json").read_text())["."]
    )


def test_bundled_contract_files_exist():
    package = ROOT / "src" / "dcc_mcp_nuke"
    assert package.joinpath("nuke_plugin", "init.py").exists()
    assert package.joinpath("nuke_plugin", "menu.py").exists()
    assert package.joinpath("skills", "nuke-script", "tools.yaml").exists()
    assert package.joinpath("skills", "nuke-script", "scripts", "open_script.py").exists()
    gizmo_scripts = package / "skills" / "nuke-node-assets" / "scripts"
    assert all(
        gizmo_scripts.joinpath(name).exists()
        for name in ("gizmo_create_from_group.py", "gizmo_instantiate.py", "gizmo_validate.py")
    )
