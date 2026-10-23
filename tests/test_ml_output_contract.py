from __future__ import annotations

import importlib.util
import json
import sys

import pytest
import yaml
from jsonschema import Draft202012Validator
from test_machine_learning import SKILL, Nuke

from dcc_mcp_nuke import machine_learning as ml


@pytest.mark.parametrize(
    "tool_name", ["inspect_ml_environment", "create_ml_node", "train_ml_node", "convert_torchscript_model"]
)
@pytest.mark.parametrize("success", [True, False])
def test_serialized_ml_skill_wrapper_matches_declared_success_and_error_envelopes(
    monkeypatch, tmp_path, tool_name, success
):
    tools = yaml.safe_load((SKILL / "tools.yaml").read_text())["tools"]
    tool = next(item for item in tools if item["name"] == tool_name)
    schema = tool["output_schema"]
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    monkeypatch.setitem(sys.modules, "nuke", Nuke())
    spec = importlib.util.spec_from_file_location(f"ml_contract_{tool_name}", SKILL / tool["source_file"])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if tool_name == "inspect_ml_environment":
        arguments = {"max_items": 1}
        payload = ml.inspect_ml_environment(Nuke(), **arguments)
    elif tool_name == "create_ml_node":
        arguments = {"node_class": "Inference"}
        payload = ml.create_ml_node(Nuke(), **arguments)
    else:
        output_key = "output_directory" if tool_name == "train_ml_node" else "output_path"
        arguments = {"node_name": "Fixture", output_key: str(tmp_path / "new-result")}
        payload = {
            "status": "completed",
            "operation": "train" if tool_name == "train_ml_node" else "convert",
            "node": "Fixture",
            "device": "cpu",
            "job_directory": str(tmp_path / "job"),
            "output_path": arguments[output_key],
            "artifacts": [{"name": "model.cat", "bytes": 1, "sha256": "0" * 64}],
            "live_graph_restored": True,
        }

    def operation(*_args, **_kwargs):
        if not success:
            raise ml.MachineLearningError("Native capability unavailable", "capability_unavailable", node="Fixture")
        return payload

    monkeypatch.setattr(module, tool_name, operation)
    # Exercise the real @skill_entry and skill_success/skill_error serialization.
    result = json.loads(json.dumps(module.main(**arguments)))
    validator.validate(result)
    assert result["success"] is success
    assert result["error"] == (None if success else "capability_unavailable")
    if success:
        assert result["context"] == payload
        assert not validator.is_valid(payload)
        required = schema["oneOf"][0]["properties"]["context"]["required"][0]
        del result["context"][required]
        assert not validator.is_valid(result)
    else:
        assert result["context"] == {"node": "Fixture"}
        result["error"] = None
        assert not validator.is_valid(result)


def pending_receipt(transport):
    # Core's REST PendingCall is minimal; direct MCP build_pending_envelope
    # adds explicit ownership, polling and transport metadata.
    result = {"job_id": "fixture-job", "status": "pending"}
    if transport == "mcp":
        result.update(
            core_job_id="fixture-job",
            job_id_owner="core",
            parent_job_id=None,
            core_poll={
                "owner": "core",
                "tool": "jobs_get_status",
                "arguments": {"job_id": "fixture-job", "include_result": True},
            },
            _meta={
                "status": "pending",
                "dcc": {
                    "jobId": "fixture-job",
                    "coreJobId": "fixture-job",
                    "jobOwner": "core",
                    "pollTool": "jobs_get_status",
                    "parentJobId": None,
                },
            },
        )
    return result


def output_validator(tool_name):
    tools = yaml.safe_load((SKILL / "tools.yaml").read_text())["tools"]
    schema = next(tool["output_schema"] for tool in tools if tool["name"] == tool_name)
    return Draft202012Validator(schema)


@pytest.mark.parametrize("tool_name", ["train_ml_node", "convert_torchscript_model"])
@pytest.mark.parametrize("transport", ["mcp", "rest"])
def test_async_ml_schema_accepts_core_initial_pending_receipts(tool_name, transport):
    validator = output_validator(tool_name)
    receipt = pending_receipt(transport)
    validator.validate(json.loads(json.dumps(receipt)))
    receipt["parent_job_id"] = "parent-job"
    receipt["runtime_metadata_extension"] = {"compatible": True}
    validator.validate(receipt)
    for field in ("job_id", "status"):
        incomplete = {key: value for key, value in receipt.items() if key != field}
        assert not validator.is_valid(incomplete)


@pytest.mark.parametrize("tool_name", ["train_ml_node", "convert_torchscript_model"])
@pytest.mark.parametrize(
    "changes",
    [
        {"job_id": ""},
        {"job_id": 42},
        {"status": None},
        {"status": "completed"},
        {"core_job_id": ""},
        {"job_id_owner": "adapter"},
        {"parent_job_id": ""},
        {"core_poll": {"owner": "core", "tool": "jobs_get_status", "arguments": {"job_id": "fixture-job"}}},
    ],
)
def test_async_ml_schema_rejects_malformed_pending_receipts(tool_name, changes):
    receipt = pending_receipt("mcp")
    receipt.update(changes)
    assert not output_validator(tool_name).is_valid(receipt)


@pytest.mark.parametrize("tool_name", ["inspect_ml_environment", "create_ml_node"])
def test_sync_ml_schema_does_not_advertise_pending_job_receipts(tool_name):
    assert not output_validator(tool_name).is_valid(pending_receipt("mcp"))
