"""The two HTTP adapters and OpenAPI use the same saved-plan projection fixtures."""

import json
import sys
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest

BCS_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BCS_ROOT))
from scripts.validate_openapi_contract import load_contract  # noqa: E402


@pytest.fixture(scope="module")
def contract():
    return load_contract(BCS_ROOT / "api-contracts/v1/openapi", entrypoint="state-machine-runs.yaml")


@pytest.fixture
def fixture():
    return json.loads((BCS_ROOT / "tests/fixtures/fixed_loop_api.json").read_text())


@pytest.mark.parametrize("key,schema", [
    ("run", "StateMachineRunView"), ("node", "StateMachineNodeRunView"),
    ("graph", "StateMachineRunGraphView"),
])
def test_shared_v2_http_projection_matches_schema(contract, fixture, key, schema):
    jsonschema.validate(fixture[key], contract[schema])


def test_start_get_rerun_and_session_detail_share_the_complete_map(contract, fixture):
    model = load_contract(BCS_ROOT / "api-contracts/v1", entrypoint="domain-models.yaml")
    expected = contract["StateMachineRunView"]["properties"]["node_execution_metadata"]
    session_run = model["SessionDetail"]["properties"]["state_machine_run"]
    assert session_run["properties"]["node_execution_metadata"] == expected
    jsonschema.validate(fixture["run"], session_run)
    assert contract["RerunStateMachineRunResponse"]["properties"]["node_execution_metadata"] == expected
    jsonschema.validate({**fixture["run"], "idempotent_replay": False}, contract["RerunStateMachineRunResponse"])
    metadata = fixture["run"]["node_execution_metadata"]
    for node in fixture["graph"]["nodes"]:
        assert node.get("execution") == metadata.get(node["node_id"])
    assert fixture["node"]["execution"] == metadata[fixture["node"]["node"]["node_id"]]


@pytest.mark.parametrize("key", ["pending_first", "pending_later"])
def test_pending_context_matches_run_metadata(contract, fixture, key):
    for pending in fixture[key]:
        jsonschema.validate(pending, contract["PendingHumanNodeView"])
        execution = fixture["run"]["node_execution_metadata"][pending["node_id"]]
        assert {k: pending["loop_context"][k] for k in ["loop_id", "iteration", "max_iterations"]} == {
            k: execution[k] for k in ["loop_id", "iteration", "max_iterations"]
        }


@pytest.mark.parametrize("field", ["definition_node_id", "loop_id", "iteration", "max_iterations"])
@pytest.mark.parametrize("key,schema,path", [
    ("run", "StateMachineRunView", ("node_execution_metadata", "historical-work-3")),
    ("node", "StateMachineNodeRunView", ("execution",)),
    ("graph", "StateMachineRunGraphView", ("nodes", 2, "execution")),
])
def test_present_execution_metadata_requires_all_fields(contract, fixture, field, key, schema, path):
    value = fixture[key]
    execution = value
    for part in path:
        execution = execution[part]
    del execution[field]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, contract[schema])


def test_v1_omits_new_fields_without_changing_existing_shapes(contract, fixture):
    run = fixture["run"]
    del run["node_execution_metadata"]
    node = fixture["node"]
    del node["execution"]
    graph = fixture["graph"]
    graph.pop("loops", None)
    graph["definition"]["graph_mode"] = "acyclic"
    del graph["definition"]["execution_graph_mode"]
    del graph["definition"]["execution_plan_compiler_version"]
    for n in graph["nodes"]:
        n.pop("execution", None)
    for edge in graph["edges"]:
        edge.pop("loop_route", None)
    for value, schema in [(run, "StateMachineRunView"), (node, "StateMachineNodeRunView"), (graph, "StateMachineRunGraphView")]:
        jsonschema.validate(value, contract[schema])
    jsonschema.validate({**run, "idempotent_replay": True}, contract["RerunStateMachineRunResponse"])


def test_graph_routes_reuse_preview_contract_and_preserve_real_outcome(contract, fixture):
    preview = load_contract(BCS_ROOT / "api-contracts/v1/openapi", entrypoint="collaboration-definitions.yaml")
    assert contract["StateMachineGraphEdgeView"]["properties"]["loop_route"] == preview["StateMachineLoopRoute"]
    graph = fixture["graph"]
    edge = next(e for e in graph["edges"] if e["loop_route"]["kind"] == "exhausted")
    assert edge["outcome"] == "again"
    assert edge["loop_route"]["logical_outcome"] == "exhausted"
    for invalid in [{}, {"kind": "exhausted"}, {"kind": "unknown", "logical_outcome": "again"}]:
        broken = deepcopy(graph)
        broken["edges"][0]["loop_route"] = invalid
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(broken, contract["StateMachineRunGraphView"])


def test_loop_descriptors_share_preview_schema_and_logical_identity(contract, fixture):
    preview = load_contract(BCS_ROOT / "api-contracts/v1/openapi", entrypoint="collaboration-definitions.yaml")
    assert contract["StateMachineRunGraphView"]["properties"]["loops"]["additionalProperties"] == preview["StateMachineLoopGraphView"]
    descriptor = fixture["graph"]["loops"]["rounds"]
    assert descriptor["body_node_ids"] == ["work"]
    for field in descriptor:
        broken = deepcopy(fixture["graph"])
        del broken["loops"]["rounds"][field]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(broken, contract["StateMachineRunGraphView"])
    descriptor["break_outcomes"] = []
    jsonschema.validate(fixture["graph"], contract["StateMachineRunGraphView"])
