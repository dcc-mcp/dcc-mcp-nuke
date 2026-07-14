from __future__ import annotations

import runpy
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "dcc_mcp_nuke"
    / "skills"
    / "nuke-script"
    / "scripts"
    / "inspect_script.py"
)


def test_unsaved_nuke_script_has_empty_path():
    namespace = runpy.run_path(str(SCRIPT))

    class UnsavedNuke:
        @staticmethod
        def scriptName():
            raise RuntimeError("no filename available, have you saved?")

    assert namespace["_script_path"](UnsavedNuke()) == ""
