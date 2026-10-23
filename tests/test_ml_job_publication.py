from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from dcc_mcp_core.cancellation import CancelToken, DccMcpCancelledError, reset_cancel_token, set_cancel_token
from test_machine_learning import Knob, Node, Nuke

from dcc_mcp_nuke import machine_learning as ml
from dcc_mcp_nuke import ml_job


@pytest.fixture
def completed_conversion(tmp_path, monkeypatch):
    executable = tmp_path / "NukeOwned.exe"
    executable.touch()
    monkeypatch.setattr(ml_job.sys, "executable", str(executable))
    monkeypatch.setattr(ml_job, "_save_snapshot", lambda *_args: None)

    def launch(args, **_kwargs):
        request = json.loads(Path(args[-1]).read_text())
        Path(request["artifacts"], "model.cat").write_bytes(b"complete native model")
        Path(request["receipt"]).write_text(json.dumps({"status": "completed", "operation": request["operation"]}))
        return SimpleNamespace(pid=123, returncode=0, poll=lambda: 0)

    monkeypatch.setattr(ml_job.subprocess, "Popen", launch)
    nuke = Nuke()
    node = Node("Converter", "CatFileCreator", {"catFile": Knob("")})
    output = tmp_path / "result.cat"
    return lambda: ml_job.run_ml_job(nuke, node, "convert", output, max_runtime_seconds=5), output


def test_copy_failure_retains_partial_only_inside_job_directory(completed_conversion, monkeypatch):
    run, output = completed_conversion
    copy_stream = ml_job._copy_stream

    def fail_copy(source, target):
        target.write(source.read(4))
        raise OSError("output volume failure")

    monkeypatch.setattr(ml_job, "_copy_stream", fail_copy)
    with pytest.raises(OSError, match="volume failure"):
        run()
    assert not output.exists()
    assert next(output.parent.glob(".dcc-ml-*/publication.cat")).read_bytes() == b"comp"
    assert next(output.parent.glob(".dcc-ml-*/artifacts/model.cat")).read_bytes() == b"complete native model"
    monkeypatch.setattr(ml_job, "_copy_stream", copy_stream)
    assert run()["status"] == "completed"
    assert output.read_bytes() == b"complete native model"


def test_corrupted_copy_fails_hash_validation_before_publication(completed_conversion, monkeypatch):
    run, output = completed_conversion
    monkeypatch.setattr(ml_job, "_copy_stream", lambda source, target: target.write(b"corrupted copied model"))
    with pytest.raises(ml.MachineLearningError, match="does not match"):
        run()
    assert not output.exists()


def test_cancelled_copy_never_publishes_final_model(completed_conversion, monkeypatch):
    run, output = completed_conversion
    token = CancelToken()
    reset = set_cancel_token(token)

    def cancelled_copy(source, target):
        target.write(source.read())
        token.cancel()

    monkeypatch.setattr(ml_job, "_copy_stream", cancelled_copy)
    try:
        with pytest.raises(DccMcpCancelledError):
            run()
    finally:
        reset_cancel_token(reset)
    assert not output.exists()


def test_publication_refuses_target_created_after_preflight(completed_conversion, monkeypatch):
    run, output = completed_conversion
    publish = ml_job._publish_file

    def raced_publication(source, target, *, overwrite):
        target.write_bytes(b"outside owner's model")
        publish(source, target, overwrite=overwrite)

    monkeypatch.setattr(ml_job, "_publish_file", raced_publication)
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        run()
    assert output.read_bytes() == b"outside owner's model"


def test_publication_exposes_verified_copy_independent_of_native_artifact(completed_conversion, monkeypatch):
    run, output = completed_conversion
    publish = ml_job._publish_file

    def checked_publication(source, target, *, overwrite):
        assert source.read_bytes() == b"complete native model"
        assert not target.exists()
        publish(source, target, overwrite=overwrite)

    monkeypatch.setattr(ml_job, "_publish_file", checked_publication)
    result = run()
    assert result["status"] == "completed"
    native = Path(result["job_directory"]) / "artifacts/model.cat"
    assert result["artifacts"][0] == ml_job._artifact(native)
    native.write_bytes(b"native artifact changed after completion")
    assert output.read_bytes() == b"complete native model"


@pytest.mark.parametrize("operation", ["train", "convert"])
def test_gui_edits_are_revalidated_in_snapshot_callback(tmp_path, monkeypatch, operation):
    executable = tmp_path / "NukeOwned.exe"
    executable.touch()
    monkeypatch.setattr(ml_job.sys, "executable", str(executable))
    monkeypatch.setattr(ml_job, "_save_snapshot", lambda *_args: pytest.fail("invalid node reached snapshot"))
    monkeypatch.setattr(
        ml_job.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("invalid node launched child")
    )
    if operation == "train":
        node = Node("Trainer", "CopyCat", {"epochs": Knob(1), "useGPUIfAvailable": Knob(False)})
        node.setInput(0, Node("Input", "Constant"))
        node.setInput(1, Node("Truth", "Constant"))

        def edit():
            node["epochs"].setValue(1000000)

        message = "integral epochs"
    else:
        model = tmp_path / "trusted.pt"
        model.write_bytes(b"trusted model")
        node = Node(
            "Converter",
            "CatFileCreator",
            {
                "torchScriptFile": Knob(str(model)),
                "channelsIn": Knob("rgba.red"),
                "channelsOut": Knob("rgba.red"),
                "modelId": Knob("model"),
            },
        )

        def edit():
            node["torchScriptFile"].setValue(str(tmp_path / "missing.pt"))

        message = "existing nonempty file"
    nuke = Nuke(nodes=[node])
    nuke.GUI = True
    callbacks = []

    def dispatch(callback):
        callbacks.append(callback)
        if len(callbacks) == 2:
            edit()
        return callback()

    nuke.executeInMainThreadWithResult = dispatch
    with pytest.raises(ml.MachineLearningError, match=message):
        if operation == "train":
            ml.train_ml_node(nuke, "Trainer", str(tmp_path / "training"))
        else:
            ml.convert_torchscript_model(nuke, "Converter", str(tmp_path / "model.cat"))
    assert len(callbacks) == 2
