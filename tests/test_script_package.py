from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

from dcc_mcp_nuke.file_references import FileReferenceRuntimeError, FileReferenceValidationError
from dcc_mcp_nuke.script_package import inspect_package, package_script

ROOT = Path(__file__).parents[1]
SKILL_ROOT = ROOT / "src" / "dcc_mcp_nuke" / "skills" / "nuke-collect"
RELATIVE = "[file dirname [value root.name]]/"


class Knob:
    def __init__(self, raw, knob_class="File_Knob", *, evaluated=None, animated=False, reject=()):
        self.raw = raw
        self.knob_class = knob_class
        self.evaluated = evaluated
        self.animated = animated
        self.reject = set(reject)
        self.writes = []

    def Class(self):
        return self.knob_class

    def getText(self):
        return self.raw

    def evaluate(self):
        return self.raw if self.evaluated is None else self.evaluated

    def isAnimated(self):
        return self.animated

    def hasExpression(self):
        return False

    def getNumKeys(self):
        return 1 if self.animated else 0

    def getKeyList(self):
        return [1.0] if self.animated else []

    def toScript(self):
        return self.raw

    def setValue(self, value):
        self.writes.append(value)
        if not any(value.startswith(prefix) for prefix in self.reject):
            self.raw = value

    def fromScript(self, value):
        self.raw = value

    def value(self):
        return 0.0 if self.knob_class == "FreeType_Knob" else self.raw


class Node:
    def __init__(self, name, knobs, node_class="Read", *, dependencies=()):
        self._name = name
        self._knobs = knobs
        self.node_class = node_class
        self.dependencies = list(dependencies)

    def fullName(self):
        return self._name

    def Class(self):
        return self.node_class

    def knobs(self):
        return self._knobs

    def fileDependencies(self, first, last):
        assert (first, last) == (1, 2)
        return self.dependencies


class Root(Node):
    def __init__(self, name, knobs=(), modified=True):
        super().__init__(name, dict(knobs), "Root")
        self._modified = modified

    def name(self):
        return self._name

    def setName(self, name):
        self._name = name

    def modified(self):
        return self._modified

    def setModified(self, value):
        self._modified = value

    def firstFrame(self):
        return 1

    def lastFrame(self):
        return 2

    def __getitem__(self, name):
        return self._knobs[name]


class Undo:
    def __init__(self):
        self.depth = 0

    def disable(self):
        self.depth += 1

    def enable(self):
        self.depth -= 1


class Nuke:
    ALL = 1
    REGISTERED = 2

    def __init__(self, nodes, script, *, root_knobs=(), plugins=(), plugin_folders=(), fail_save=False):
        self.nodes = list(nodes)
        self._root = Root(str(script), root_knobs)
        self._plugins = list(plugins)
        self._plugin_folders = list(plugin_folders)
        self.fail_save = fail_save
        self.Undo = Undo()
        self.saves = []

    def root(self):
        return self._root

    def allNodes(self, *, group, recurseGroups):
        assert group is self._root and recurseGroups is True
        return self.nodes

    def plugins(self, switches):
        assert switches == self.ALL | self.REGISTERED
        return self._plugins

    def pluginPath(self):
        return self._plugin_folders

    def scriptSaveToTemp(self, path):
        self.saves.append(path)
        self._root.setModified(False)
        if self.fail_save:
            self._root.setName("changed-by-failing-host.nk")
            raise RuntimeError("private/host/failure")
        content = {
            node.fullName(): {name: knob.toScript() for name, knob in node.knobs().items()} for node in self.nodes
        }
        Path(path).write_text(json.dumps(content), encoding="utf-8")
        return path


def _source(tmp_path, name="plate.exr", content=b"pixels"):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _nuke(tmp_path, *sources, **kwargs):
    script = _source(tmp_path, "original.nk", b"original disk script")
    nodes = [Node(f"Read{i}", {"file": Knob(str(source))}) for i, source in enumerate(sources)]
    return Nuke(nodes, script, **kwargs)


def test_preview_reports_sequence_files_bytes_and_does_not_mutate(tmp_path):
    _source(tmp_path, "plate.0001.exr", b"abc")
    _source(tmp_path, "plate.0002.exr", b"12345")
    nuke = _nuke(tmp_path, tmp_path / "plate.%04d.exr")
    nuke.nodes[0].knobs()["file"].evaluated = str(tmp_path / "plate.0000.exr")
    target = tmp_path / "package"

    result = package_script(nuke, str(target), max_report_items=1)

    assert result["dry_run"] is True
    assert result["files_count"] == 2
    assert result["total_size_bytes"] == 8
    assert result["files_truncated"] is True
    assert not target.exists()
    assert not nuke.saves
    assert nuke.nodes[0].knobs()["file"].writes == []


def test_directory_package_restores_graph_root_and_original_disk_script(tmp_path):
    plate = _source(tmp_path)
    geometry = _source(tmp_path, "camera.abc", b"camera")
    nuke = _nuke(tmp_path, plate, geometry)
    before = [node.knobs()["file"].toScript() for node in nuke.nodes]
    original_name = nuke.root().name()
    target = tmp_path / "package"

    result = package_script(nuke, str(target), dry_run=False)

    assert result["verified"] is True
    assert result["files_copied"] == 2
    assert [node.knobs()["file"].toScript() for node in nuke.nodes] == before
    assert nuke.root().name() == original_name
    assert nuke.root().modified() is True
    assert nuke.Undo.depth == 0
    assert Path(original_name).read_bytes() == b"original disk script"
    content = json.loads((target / "script.nk").read_text(encoding="utf-8"))
    assert all(item["file"].startswith(RELATIVE + "assets/") for item in content.values())
    for item in result["files"]:
        assert (target / item["relative_path"]).read_bytes() == Path(item["source"]).read_bytes()
    assert inspect_package(str(target))["verified"] is True


def test_zip_package_and_identical_basenames_are_isolated(tmp_path):
    first = _source(tmp_path, "a/plate.exr", b"first")
    second = _source(tmp_path, "b/plate.exr", b"second")
    target = tmp_path / "package.zip"
    result = package_script(_nuke(tmp_path, first, second), str(target), dry_run=False)
    assert len({item["relative_path"] for item in result["files"]}) == 2
    with zipfile.ZipFile(target) as archive:
        assert "script.nk" in archive.namelist()
        assert "manifest.json" in archive.namelist()
        assert len(json.loads(archive.read("manifest.json"))["files"]) == 2
    assert inspect_package(str(target))["verified"] is True


def test_root_config_font_gizmo_and_runtime_requirements_are_reported(tmp_path):
    config = _source(tmp_path, "config.ocio", b"ocio config")
    font = _source(tmp_path, "plugins/fonts/UtopiaRegular.pfa", b"font")
    gizmo = _source(tmp_path, "plugins/MyGizmo.gizmo", b"Gizmo {}")
    binary = _source(tmp_path, "plugins/ThirdParty.dll", b"binary")
    font_knob = Knob("{ Utopia : Regular : UtopiaRegular.pfa : 0 }", "FreeType_Knob")
    nuke = _nuke(
        tmp_path,
        root_knobs={"customOCIOConfigPath": Knob(str(config))},
        plugins=[str(gizmo), str(binary), "Read"],
        plugin_folders=[str(tmp_path / "plugins")],
    )
    nuke.nodes = [
        Node("Text1", {"font": font_knob}, "Text2"),
        Node("Gizmo1", {}, "MyGizmo"),
        Node("Third1", {}, "ThirdParty"),
    ]
    target = tmp_path / "package"

    result = package_script(nuke, str(target), dry_run=False)

    assert {Path(item["source"]) for item in result["files"]} == {config, font, gizmo}
    assert any(item["reason"] == "ocio_dependent_luts_not_resolved" for item in result["unrewritten"])
    assert any(item["class"] == "ThirdParty" and item["included"] is None for item in result["runtime_dependencies"])
    assert len(result["plugin_directories"]) == 2
    copied = json.loads((target / "script.nk").read_text(encoding="utf-8"))
    assert copied["Text1"]["font"] == "{ Utopia : Regular : UtopiaRegular.pfa : 0 }"
    assert font_knob.toScript() == "{ Utopia : Regular : UtopiaRegular.pfa : 0 }"


def test_expression_animated_missing_and_unsupported_fonts_are_explicit(tmp_path):
    plate = _source(tmp_path)
    expression = Knob("[python plate_path()]", evaluated=str(plate))
    animated = Knob(str(plate), animated=True)
    unsupported_font = Knob("unknown native font form", "FreeType_Knob")
    nuke = _nuke(tmp_path)
    nuke.nodes = [
        Node("Read1", {"file": expression}),
        Node("Read2", {"file": animated}),
        Node("Read3", {"file": Knob(str(tmp_path / "missing.exr"))}),
        Node("Text1", {"font": unsupported_font}, "Text2"),
    ]
    result = package_script(nuke, str(tmp_path / "package"))
    assert result["missing_count"] == 1
    assert {item["reason"] for item in result["unrewritten"]} == {
        "expression",
        "animated",
        "unsupported_font_serialization",
    }
    assert result["rewrite_count"] == 0


def test_node_label_fonts_are_not_render_dependencies(tmp_path):
    nuke = _nuke(tmp_path)
    nuke.nodes = [Node("Read1", {"note_font": Knob("Helvetica", "Font_Knob")})]
    result = package_script(nuke, str(tmp_path / "package"))
    assert result["unrewritten_count"] == result["missing_count"] == result["files_count"] == 0


def test_derived_root_ocio_path_is_never_touched_but_custom_config_is_collected(tmp_path):
    class ReadOnlyDerivedKnob(Knob):
        def setValue(self, value):
            raise ValueError("read only variable")

        def fromScript(self, value):
            raise ValueError("read only variable")

    config = _source(tmp_path, "config.ocio", b"ocio")
    derived = ReadOnlyDerivedKnob(str(config))
    custom = Knob(str(config))
    nuke = _nuke(tmp_path, root_knobs={"OCIOConfigPath": derived, "customOCIOConfigPath": custom})
    result = package_script(nuke, str(tmp_path / "package"), dry_run=False)
    assert result["verified"] is True
    assert result["files_count"] == 1
    assert {item["reason"] for item in result["unrewritten"]} == {
        "host_managed_ocio_config",
        "ocio_dependent_luts_not_resolved",
    }
    assert not derived.writes
    assert custom.raw == str(config)


def test_outputs_are_redirected_in_copied_script_without_copying_existing_renders(tmp_path):
    render = _source(tmp_path, "renders/output.exr", b"render")
    nuke = _nuke(tmp_path)
    nuke.nodes = [Node("Write1", {"file": Knob(str(render))}, "Write")]
    target = tmp_path / "package"
    result = package_script(nuke, str(target), dry_run=False)
    assert result["files_count"] == 0
    copied = json.loads((target / "script.nk").read_text(encoding="utf-8"))
    assert copied["Write1"]["file"] == RELATIVE + "renders/Write1/output.exr"
    assert (target / "renders/Write1").is_dir()
    assert render.read_bytes() == b"render"


def test_used_frames_follow_nuke_dependency_query_including_time_shift_and_missing(tmp_path):
    _source(tmp_path, "plate.0001.exr")
    ninth = _source(tmp_path, "plate.0009.exr")
    missing = tmp_path / "plate.0010.exr"
    read = Node("Read1", {"file": Knob(str(tmp_path / "plate.%04d.exr"))})
    write = Node("Write1", {}, "Write", dependencies=[(read, [str(ninth), str(missing)])])
    nuke = _nuke(tmp_path)
    nuke.nodes = [read, write]
    result = package_script(nuke, str(tmp_path / "package"), frame_mode="used")
    assert [Path(item["source"]).name for item in result["files"]] == ["plate.0009.exr"]
    assert Path(result["missing"][0]["path"]).name == missing.name


def test_used_mode_refuses_unproven_sequence_range(tmp_path):
    _source(tmp_path, "plate.0001.exr")
    nuke = _nuke(tmp_path, tmp_path / "plate.####.exr")
    with pytest.raises(FileReferenceRuntimeError, match="could not be proven"):
        package_script(nuke, str(tmp_path / "package"), frame_mode="used", dry_run=False)
    assert not nuke.saves


def test_serialization_failure_restores_all_touched_knobs_and_root(tmp_path):
    source = _source(tmp_path)
    nuke = _nuke(tmp_path, source, fail_save=True)
    original = nuke.nodes[0].knobs()["file"].toScript()
    name = nuke.root().name()
    target = tmp_path / "package"
    with pytest.raises(FileReferenceRuntimeError, match="serialize"):
        package_script(nuke, str(target), dry_run=False)
    assert nuke.nodes[0].knobs()["file"].toScript() == original
    assert nuke.root().name() == name
    assert nuke.root().modified() is True
    assert nuke.Undo.depth == 0
    assert not target.exists()
    assert not list(tmp_path.glob(".dcc-mcp-collect-*"))


def test_path_readback_failure_restores_every_prior_rewrite(tmp_path):
    first = _source(tmp_path, "first.exr")
    second = _source(tmp_path, "second.exr")
    nuke = _nuke(tmp_path, first, second)
    nuke.nodes[1].knobs()["file"].reject.add(RELATIVE)
    with pytest.raises(FileReferenceRuntimeError, match="serialize"):
        package_script(nuke, str(tmp_path / "package"), dry_run=False)
    assert nuke.nodes[0].knobs()["file"].raw == str(first)
    assert nuke.nodes[1].knobs()["file"].raw == str(second)
    assert nuke.Undo.depth == 0


def test_failed_rollback_is_reported_before_publication(tmp_path):
    class FailingRestore(Knob):
        def fromScript(self, value):
            raise RuntimeError("private error")

    source = _source(tmp_path)
    nuke = _nuke(tmp_path)
    nuke.nodes = [Node("Read1", {"file": FailingRestore(str(source))})]
    target = tmp_path / "package"
    with pytest.raises(FileReferenceRuntimeError, match="rollback could not be verified"):
        package_script(nuke, str(target), dry_run=False)
    assert not target.exists()
    assert nuke.Undo.depth == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_files": 1},
        {"max_total_bytes": 1},
        {"max_references": 1},
        {"max_files": True},
        {"first_frame": 3, "last_frame": 2},
    ],
)
def test_resource_limits_fail_before_host_mutation(tmp_path, kwargs):
    first = _source(tmp_path, "first.exr")
    second = _source(tmp_path, "second.exr")
    nuke = _nuke(tmp_path, first, second)
    with pytest.raises(FileReferenceValidationError):
        package_script(nuke, str(tmp_path / "package"), dry_run=False, **kwargs)
    assert not nuke.saves
    assert all(not node.knobs()["file"].writes for node in nuke.nodes)


def test_existing_target_is_preserved_and_parent_must_exist(tmp_path):
    target = tmp_path / "existing"
    target.mkdir()
    sentinel = _source(target, "sentinel", b"preserve")
    with pytest.raises(FileReferenceValidationError, match="already exists"):
        package_script(_nuke(tmp_path), str(target), dry_run=False)
    assert sentinel.read_bytes() == b"preserve"
    with pytest.raises(FileReferenceValidationError, match="parent"):
        package_script(_nuke(tmp_path), str(tmp_path / "missing" / "package"))


def test_oversized_receipt_is_rejected_during_preview_before_copying(tmp_path, monkeypatch):
    import dcc_mcp_nuke.script_package as module

    monkeypatch.setattr(module, "_MAX_MANIFEST_BYTES", 100)
    nuke = _nuke(tmp_path, _source(tmp_path))
    with pytest.raises(FileReferenceValidationError, match="manifest exceeds"):
        package_script(nuke, str(tmp_path / "package"))
    assert not nuke.saves
    assert not (tmp_path / "package").exists()


def test_inspection_rejects_modified_file_size_and_unsafe_receipt_names(tmp_path):
    target = tmp_path / "package"
    result = package_script(_nuke(tmp_path, _source(tmp_path)), str(target), dry_run=False)
    (target / result["files"][0]["relative_path"]).write_bytes(b"changed contents and size")
    with pytest.raises(FileReferenceRuntimeError, match="size"):
        inspect_package(str(target))
    receipt = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    receipt["files"][0]["relative_path"] = "../original.nk"
    (target / "manifest.json").write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(FileReferenceValidationError, match="escapes"):
        inspect_package(str(target))


def test_inspection_rejects_unsafe_unlisted_zip_entries(tmp_path):
    target = tmp_path / "package.zip"
    package_script(_nuke(tmp_path), str(target), dry_run=False)
    with zipfile.ZipFile(target, "a") as archive:
        archive.writestr("../escape", "x")
    with pytest.raises(FileReferenceValidationError, match="escapes"):
        inspect_package(str(target))


def test_cancelled_copy_cleans_staging_and_leaves_graph_untouched(tmp_path, monkeypatch):
    import dcc_mcp_nuke.script_package as module

    source = _source(tmp_path)
    nuke = _nuke(tmp_path, source)
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise RuntimeError("cancelled")

    monkeypatch.setattr(module, "check_cancelled", cancel)
    with pytest.raises(RuntimeError, match="cancelled"):
        package_script(nuke, str(tmp_path / "package"), dry_run=False)
    assert not list(tmp_path.glob(".dcc-mcp-collect-*"))
    assert not nuke.saves
    assert nuke.nodes[0].knobs()["file"].raw == str(source)


def test_collect_skill_contract_declares_async_main_thread_and_readback():
    from dcc_mcp_core import validate_skill
    from dcc_mcp_core.verification.lint import find_unpaired_write_verbs

    report = validate_skill(str(SKILL_ROOT))
    assert report.is_clean, [issue.message for issue in report.issues]
    manifest = yaml.safe_load((SKILL_ROOT / "tools.yaml").read_text(encoding="utf-8"))
    package = manifest["tools"][0]
    assert package["execution"] == "async"
    assert package["affinity"] == "main"
    assert package["input_schema"]["properties"]["dry_run"]["default"] is True
    assert find_unpaired_write_verbs(manifest["tools"]) == []


def test_collect_route_preview_and_safe_failures(tmp_path, monkeypatch):
    nuke = _nuke(tmp_path, _source(tmp_path))
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    spec = importlib.util.spec_from_file_location("collect_route", SKILL_ROOT / "scripts" / "package_script.py")
    route = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(route)
    result = route.main.__wrapped__(output_path=str(tmp_path / "package"))
    assert result["success"] is True
    assert result["context"]["dry_run"] is True
    nuke.root = lambda: (_ for _ in ()).throw(OSError("private/host/path"))
    result = route.main.__wrapped__(output_path=str(tmp_path / "package"))
    assert result["success"] is False
    assert "private" not in str(result)


def test_server_loads_collect_routes_with_main_thread_affinity(monkeypatch):
    from dcc_mcp_nuke.dispatcher import NukeDispatcher
    from dcc_mcp_nuke.server import NukeMcpServer

    monkeypatch.setattr(NukeDispatcher, "start", lambda _self: None)
    monkeypatch.setattr(NukeDispatcher, "stop", lambda _self: None)
    server = NukeMcpServer(port=0)
    try:
        server.register_builtin_actions()
        assert server.load_skill("nuke-collect") is True
        action = next(item for item in server.list_actions() if item["name"] == "nuke_collect__package_script")
        assert action["thread_affinity"] == "main"
        assert action["enforce_thread_affinity"] is True
    finally:
        server.stop()
