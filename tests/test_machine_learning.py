from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from dcc_mcp_core import validate_skill
from dcc_mcp_core.cancellation import CancelToken, DccMcpCancelledError, reset_cancel_token, set_cancel_token
from test_node_graph_contract import FakeKnob, FakeNode, FakeNuke

from dcc_mcp_nuke import machine_learning as ml
from dcc_mcp_nuke.ml_job import _wait_owned

SKILL = Path(__file__).parents[1] / "src/dcc_mcp_nuke/skills/nuke-machine-learning"


class Knob(FakeKnob):
    def values(self):
        return ["Small", "Medium", "Large", "32", "64", "128", "256", "512", "Manual", "Auto", "None", "1024"]

    def getText(self):
        return self.current

    def value(self, index=None):
        return self.current if index is None else self.current[index]


class Node(FakeNode):
    def __init__(self, name, node_class, knobs=None):
        super().__init__(name, node_class, knobs=knobs, input_count=0 if node_class == "CatFileCreator" else 2)
        self.selected = False

    def fullName(self):
        return self.name()

    def isSelected(self):
        return self.selected

    def setSelected(self, value):
        self.selected = value


class Root:
    def __init__(self):
        self.dirty = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def modified(self):
        return self.dirty

    def setModified(self, value):
        self.dirty = value

    def name(self):
        return "Root"


class Nuke(FakeNuke):
    NUKE_VERSION_STRING = "16.1v2"
    GUI = False
    ALL = 1
    REGISTERED = 2

    def __init__(self, *, flavor="nukex", nodes=None):
        super().__init__(nodes or [])
        self.env = {"nukex": flavor == "nukex", "studio": flavor == "nukestudio"}
        self.root_node = Root()
        self.created = []

    def root(self):
        return self.root_node

    def createNode(self, node_class, inpanel=False):
        assert not inpanel
        fields = ml._FIELDS[node_class]
        node = Node(node_class, node_class, {name: Knob(1 if name == "epochs" else "") for name in fields})
        node._knobs["useGPUIfAvailable"] = Knob(True)
        self.nodes.append(node)
        self.created.append(node)
        self.root_node.dirty = True
        return node

    def pluginPath(self):
        return []

    def plugins(self, mode=None):
        return list(ml.AIR_CLASSES) if mode == self.REGISTERED else []


@pytest.mark.parametrize("node_class", ml.AIR_CLASSES)
def test_all_six_additive_nodes_preserve_existing_selection_and_resolve_names(node_class):
    sentinel = Node("Existing", "NoOp")
    sentinel.setSelected(True)
    nuke = Nuke(nodes=[sentinel])
    first = ml.create_ml_node(nuke, node_class, name="Air")
    second = ml.create_ml_node(nuke, node_class, name="Air")
    assert (first["node"], second["node"]) == ("Air", "Air1")
    assert nuke.toNode("Existing") is sentinel and sentinel.isSelected()
    assert all(not node.isSelected() for node in nuke.created)
    assert first["execution_started"] is False
    if node_class != "CatFileCreator":
        assert nuke.created[0]["useGPUIfAvailable"].value() is False


@pytest.mark.parametrize("node_class", ml.X_CLASSES)
def test_plain_nuke_refuses_x_operations_before_creation(node_class):
    nuke = Nuke(flavor="nuke")
    with pytest.raises(ml.MachineLearningError) as error:
        ml.create_ml_node(nuke, node_class)
    assert error.value.code == "capability_unavailable"
    assert not nuke.created and not nuke.root().modified()


@pytest.mark.parametrize("node_class,version", [("BigCat", "16.0v6"), ("CatFileCreator", "13.0v1")])
def test_version_gate_refuses_unavailable_classes_before_mutation(node_class, version):
    nuke = Nuke()
    nuke.NUKE_VERSION_STRING = version
    with pytest.raises(ml.MachineLearningError, match="version"):
        ml.create_ml_node(nuke, node_class)
    assert not nuke.created


def test_native_enum_failure_rolls_back_node_dirty_and_selection():
    sentinel = Node("Existing", "NoOp")
    sentinel.setSelected(True)
    nuke = Nuke(nodes=[sentinel])
    with pytest.raises(ml.MachineLearningError, match="enumeration"):
        ml.create_ml_node(nuke, "Upscale", settings={"tileSize": "invented"})
    assert nuke.nodes == [sentinel] and sentinel.isSelected()
    assert not nuke.root().modified()


@pytest.mark.parametrize(
    "settings", [{"epochs": 0}, {"epochs": True}, {"epochs": 1.2}, {"startTraining": "execute"}, {"modelSize": False}]
)
def test_training_settings_fail_before_mutation(settings):
    nuke = Nuke()
    with pytest.raises(ml.MachineLearningError):
        ml.create_ml_node(nuke, "CopyCat", settings=settings)
    assert not nuke.created


def test_explicit_training_inputs_are_retained_without_implicit_connections():
    source = Node("Source", "Constant")
    truth = Node("Truth", "Constant")
    nuke = Nuke(nodes=[source, truth])
    created = ml.create_ml_node(
        nuke, "CopyCat", input_node="Source", reference_node="Truth", settings={"epochs": 1, "cropSize": "32"}
    )
    node = nuke.toNode(created["node"])
    assert node.input(0) is source and node.input(1) is truth
    assert all(source.input(index) is None for index in range(source.inputs()))


def test_model_inspection_does_not_read_or_load_model_bytes(tmp_path, monkeypatch):
    (tmp_path / "model.cat").write_bytes(b"model is never deserialized")
    (tmp_path / "model.pt").write_bytes(b"model is never executed")
    monkeypatch.setattr(Path, "read_bytes", lambda *_args: pytest.fail("inspection read model contents"))
    nuke = Nuke(flavor="nuke")
    result = ml.inspect_ml_environment(nuke, model_directories=[str(tmp_path)], max_items=1)
    assert result["model_asset_count"] == 2 and result["models_truncated"]
    assert not result["model_loading_performed"] and not nuke.created
    assert {row["node_class"] for row in result["nodes"] if not row["host_version_and_flavor_allowed"]} == set(
        ml.X_CLASSES
    )


def test_metadata_scan_honors_entry_bound(tmp_path):
    for index in range(5):
        (tmp_path / f"{index}.cat").write_bytes(b"x")
    result = ml.inspect_ml_environment(Nuke(), model_directories=[str(tmp_path)], max_scan_entries=2)
    assert result["scanned_entries"] == 2 and result["scan_incomplete"]
    assert result["model_asset_count"] == 2


def test_paths_and_channel_expressions_fail_closed(tmp_path):
    for path in ("relative.cat", str(tmp_path / "[value root.name].cat")):
        with pytest.raises(ml.MachineLearningError):
            ml._path(path)
    for channels in ([{}], ["red"], ["rgba.red", "rgba.red"], ["[exec foo]"]):
        with pytest.raises(ml.MachineLearningError):
            ml._settings("CatFileCreator", {"channelsIn": channels})


def test_gui_native_preflight_is_dispatched_to_main_thread():
    nuke = Nuke()
    nuke.GUI = True
    seen = []
    nuke.executeInMainThreadWithResult = lambda callback: seen.append("main") or callback()
    assert ml._on_host_thread(nuke, lambda: 42) == 42
    assert seen == ["main"]


def test_conversion_rejects_native_required_model_id_before_child(tmp_path, monkeypatch):
    from dcc_mcp_nuke import ml_job

    model = tmp_path / "tiny.pt"
    model.write_bytes(b"caller selected model")
    nuke = Nuke()
    result = ml.create_ml_node(
        nuke,
        "CatFileCreator",
        settings={"torchScriptFile": str(model), "channelsIn": ["rgba.red"], "channelsOut": ["rgba.red"]},
    )
    monkeypatch.setattr(ml_job, "run_ml_job", lambda *_args, **_kwargs: pytest.fail("should fail before launch"))
    with pytest.raises(ml.MachineLearningError, match="nonempty modelId"):
        ml.convert_torchscript_model(nuke, result["node"], str(tmp_path / "out.cat"))


def test_owned_process_timeout_stops_only_owned_child():
    process = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    with pytest.raises(ml.MachineLearningError) as error:
        _wait_owned(process, 0)
    assert error.value.code == "timeout" and process.poll() is not None


def test_shared_core_cancellation_stops_owned_child():
    process = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    token = CancelToken()
    reset = set_cancel_token(token)
    token.cancel()
    try:
        with pytest.raises(DccMcpCancelledError):
            _wait_owned(process, 30)
    finally:
        reset_cancel_token(reset)
    assert process.poll() is not None


def test_failure_writing_launch_receipt_stops_owned_child(tmp_path, monkeypatch):
    from dcc_mcp_nuke import ml_job

    process = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    executable = tmp_path / "NukeOwned.exe"
    executable.touch()
    monkeypatch.setattr(ml_job.sys, "executable", str(executable))
    monkeypatch.setattr(ml_job.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(ml_job, "_save_snapshot", lambda *_args: None)
    write_text = Path.write_text

    def write(path, *args, **kwargs):
        if path.name == "launch.json":
            raise OSError("full evidence volume")
        return write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write)
    nuke = Nuke()
    node = Node("Trainer", "CopyCat", {"dataDirectory": Knob("")})
    with pytest.raises(OSError, match="evidence volume"):
        ml_job.run_ml_job(nuke, node, "train", tmp_path / "result", max_runtime_seconds=5)
    assert process.poll() is not None


def test_worker_restores_original_cwd_root_name_and_plugin_precedence_before_execution(tmp_path, monkeypatch):
    from dcc_mcp_nuke.ml_worker import execute_request

    calls = []
    root = type(
        "WorkerRoot",
        (),
        {
            "name": lambda self: self.current,
            "__getitem__": lambda self, key: type(
                "RootNameKnob", (), {"setValue": lambda knob, value: setattr(self, "current", value)}
            )(),
        },
    )()
    original_name = str(tmp_path / "original.nk")
    root.current = "Root"
    node = Node("Trainer", "CopyCat", {"useGPUIfAvailable": Knob(True)})

    class Worker:
        def pluginAddPath(self, path):
            assert Path.cwd() == tmp_path
            calls.append(("plugin", path))

        def scriptOpen(self, path):
            assert calls == [("plugin", "builtins"), ("plugin", "custom")]
            calls.append(("open", path))
            root.current = path

        def root(self):
            return root

        def toNode(self, name):
            assert root.name() == original_name
            return node if name == "Trainer" else None

        def execute(self, handle, first, last):
            assert root.name() == original_name and Path.cwd() == tmp_path
            assert handle is node and not node["useGPUIfAvailable"].value()
            calls.append(("execute", first, last))

    monkeypatch.chdir(tmp_path)
    execute_request(
        Worker(),
        {
            "operation": "train",
            "node": "Trainer",
            "node_class": "CopyCat",
            "snapshot": "snapshot.nk",
            "source_context": {
                "cwd": str(tmp_path),
                "root_name": original_name,
                "plugin_paths": ["custom", "builtins"],
            },
        },
    )
    assert calls[-1] == ("execute", 1, 1)
    assert not (tmp_path / "original.nk").exists()


def test_ml_skill_has_bounded_typed_surface_and_shared_worker_jobs():
    report = validate_skill(str(SKILL))
    assert report.is_clean, [issue.message for issue in report.issues]
    tools = {tool["name"]: tool for tool in yaml.safe_load((SKILL / "tools.yaml").read_text())["tools"]}
    assert set(tools) == {"inspect_ml_environment", "create_ml_node", "train_ml_node", "convert_torchscript_model"}
    assert all(tool["input_schema"]["additionalProperties"] is False for tool in tools.values())
    for name in ("train_ml_node", "convert_torchscript_model"):
        assert tools[name]["execution"] == "async" and tools[name]["affinity"] == "any"
        assert tools[name]["input_schema"]["properties"]["max_runtime_seconds"]["maximum"] == 120
