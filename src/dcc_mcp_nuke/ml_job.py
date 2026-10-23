"""One bounded native subprocess, using the caller's shared Core job token."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from dcc_mcp_core.cancellation import check_cancelled

from dcc_mcp_nuke.machine_learning import MachineLearningError, _on_host_thread
from dcc_mcp_nuke.script_package import _save_snapshot


def _stop_owned(process: Any) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _wait_owned(process: Any, timeout: int) -> int:
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            check_cancelled()
            if time.monotonic() >= deadline:
                raise MachineLearningError(
                    "native ML job exceeded its time limit; partial artifacts were preserved", "timeout"
                )
            time.sleep(0.1)
        check_cancelled()
        return process.returncode
    finally:
        _stop_owned(process)


def run_ml_job(nuke: Any, node: Any, operation: str, output: Path, *, max_runtime_seconds: int) -> dict[str, Any]:
    """Snapshot on the host thread, then monitor only our matching NukeX child."""
    if type(max_runtime_seconds) is not int or not 1 <= max_runtime_seconds <= 120:
        raise MachineLearningError("max_runtime_seconds must be an integer from 1 through 120")
    if operation not in {"train", "convert"}:
        raise MachineLearningError("unsupported ML operation")
    if output.exists() or not output.parent.is_dir():
        raise MachineLearningError("output must not exist and its parent directory must exist")
    executable = Path(sys.executable).resolve()
    if not executable.is_file() or not executable.name.lower().startswith("nuke"):
        raise MachineLearningError("a matching native Nuke executable is required", "capability_unavailable")
    check_cancelled()
    if operation == "train":
        output.mkdir()
        directory = output
    else:
        directory = Path(tempfile.mkdtemp(prefix=".dcc-ml-", dir=str(output.parent)))
    artifacts = directory / "artifacts"
    artifacts.mkdir()
    destination = artifacts if operation == "train" else artifacts / "model.cat"
    snapshot = directory / "snapshot.nk"
    receipt_path = directory / "result.json"
    try:

        def prepare():
            original_name = nuke.root().name()
            plugin_paths = nuke.pluginPath()
            if (
                not isinstance(original_name, str)
                or not original_name
                or len(original_name) > 8192
                or "\x00" in original_name
                or not isinstance(plugin_paths, list)
                or len(plugin_paths) > 1024
                or any(
                    not isinstance(path, str) or not path or len(path) > 8192 or "\x00" in path for path in plugin_paths
                )
            ):
                raise MachineLearningError("source script/plugin context is unavailable", "capability_unavailable")
            source_context = {
                "root_name": original_name,
                "cwd": os.getcwd(),
                "plugin_paths": list(dict.fromkeys(os.path.abspath(path) for path in plugin_paths)),
            }
            knob = node["dataDirectory" if operation == "train" else "catFile"]
            _save_snapshot(nuke, snapshot, [(knob, destination.as_posix(), False)])
            return {
                "operation": operation,
                "node": node.fullName(),
                "node_class": node.Class(),
                "snapshot": str(snapshot),
                "receipt": str(receipt_path),
                "artifacts": str(artifacts),
                "source_context": source_context,
            }

        request = _on_host_thread(nuke, prepare)
        request_path = directory / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        environment = os.environ.copy()
        for key, name in (
            ("HOME", "home"),
            ("TEMP", "temp"),
            ("TMP", "temp"),
            ("NUKE_TEMP_DIR", "nuke-temp"),
            ("NUKE_PATH", "empty-profile"),
        ):
            location = directory / name
            location.mkdir(exist_ok=True)
            environment[key] = str(location)
        (directory / "home" / ".nuke").mkdir()
        environment["DCC_MCP_GATEWAY_PORT"] = "0"
        environment["CUDA_VISIBLE_DEVICES"] = ""
        worker = Path(__file__).with_name("ml_worker.py")
        check_cancelled()
        with (directory / "stdout.log").open("wb") as stdout, (directory / "stderr.log").open("wb") as stderr:
            process = subprocess.Popen(
                [str(executable), "--nukex", "-i", "-t", str(worker), str(request_path)],
                cwd=str(directory),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                (directory / "launch.json").write_text(
                    json.dumps(
                        {
                            "pid": process.pid,
                            "executable": str(executable),
                            "operation": operation,
                            "max_runtime_seconds": max_runtime_seconds,
                        }
                    ),
                    encoding="utf-8",
                )
                code = _wait_owned(process, max_runtime_seconds)
            finally:
                _stop_owned(process)
        if code != 0 or not receipt_path.is_file():
            raise MachineLearningError(
                "native ML execution failed; inspect the retained job logs", "native_execution_failed", exit_code=code
            )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("status") != "completed" or receipt.get("operation") != operation:
            raise MachineLearningError("native ML job did not report completion", "native_execution_failed")
        files = sorted(path for path in artifacts.iterdir() if path.is_file())
        if len(files) > 128 or not any(path.suffix == ".cat" and path.stat().st_size > 0 for path in files):
            raise MachineLearningError(
                "native ML job did not produce a bounded model result", "native_execution_failed"
            )
        check_cancelled()
        if operation == "convert":
            with destination.open("rb") as source, output.open("xb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
        return {
            "status": "completed",
            "operation": operation,
            "node": request["node"],
            "device": "cpu",
            "job_directory": str(directory),
            "output_path": str(output),
            "artifacts": [_artifact(path) for path in files],
            "live_graph_restored": True,
        }
    except BaseException as exc:
        if isinstance(exc, MachineLearningError):
            exc.context["job_directory"] = str(directory)
        raise


def _artifact(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            check_cancelled()
            digest.update(block)
    return {"name": path.name, "bytes": path.stat().st_size, "sha256": digest.hexdigest()}
