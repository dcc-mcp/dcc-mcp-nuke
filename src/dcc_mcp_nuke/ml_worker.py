"""Private matching-Nuke worker for the two explicit AIR execution operations."""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path


def execute_request(nuke, request):
    """Restore the source evaluation/search context without saving its script."""
    context = request["source_context"]
    os.chdir(context["cwd"])
    # Outside init.py this reorders search paths without running startup scripts.
    for path in reversed(context["plugin_paths"]):
        nuke.pluginAddPath(path)
    nuke.scriptOpen(request["snapshot"])
    nuke.root()["name"].setValue(context["root_name"])
    if nuke.root().name() != context["root_name"]:
        raise RuntimeError("source script name could not be restored")
    node = nuke.toNode(request["node"])
    expected = {"train": {"CopyCat", "BigCat"}, "convert": {"CatFileCreator"}}
    if (
        node is None
        or node.Class() != request["node_class"]
        or node.Class() not in expected.get(request["operation"], set())
    ):
        raise RuntimeError("snapshot AIR node does not match the request")
    if request["operation"] == "train":
        node["useGPUIfAvailable"].setValue(False)
    nuke.execute(node, 1, 1)


def main() -> int:
    import nuke

    request = json.loads(Path(sys.argv[-1]).read_text(encoding="utf-8"))
    receipt = {"operation": request["operation"], "status": "failed"}
    code = 1
    try:
        if not nuke.env.get("nukex") and not nuke.env.get("studio"):
            raise RuntimeError("NukeX or Nuke Studio is required")
        execute_request(nuke, request)
        receipt["status"] = "completed"
        code = 0
    except Exception as exc:
        receipt["error_type"] = type(exc).__name__
        traceback.print_exc(file=sys.stderr)
    finally:
        Path(request["receipt"]).write_text(json.dumps(receipt), encoding="utf-8")
        nuke.root().setModified(False)
        nuke.scriptExit()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
