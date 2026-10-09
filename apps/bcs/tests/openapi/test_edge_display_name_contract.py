"""Optional graph labels are display metadata, independent of route outcomes."""

import json
import sys
from pathlib import Path

import jsonschema
import pytest

BCS_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BCS_ROOT))
from scripts.validate_openapi_contract import load_contract  # noqa: E402


@pytest.fixture(params=["preview_edge", "run_edge", "loop", "run_role"])
def label_contract(request):
    graph = json.loads((BCS_ROOT / "tests/fixtures/fixed_loop_api.json").read_text())["graph"]
    is_run = request.param in {"run_edge", "run_role"}
    contract = load_contract(BCS_ROOT / "api-contracts/v1/openapi",
                             entrypoint="state-machine-runs.yaml" if is_run else "collaboration-definitions.yaml")
    if request.param == "loop":
        return graph["loops"]["rounds"], "continue_display_name", contract["StateMachineLoopGraphView"]
    if request.param == "run_role":
        return graph["nodes"][0], "assignee_display_name", contract["StateMachineGraphNodeView"]
    schema = contract["StateMachineGraphEdgeView" if is_run else "CollaborationDefinitionGraphEdge"]
    return graph["edges"][-1], "display_name", schema


def test_optional_label_preserves_legacy_shape_and_actual_outcome(label_contract):
    value, field, schema = label_contract
    jsonschema.validate(value, schema)
    original_outcome = value.get("outcome")
    value[field] = "处理未通过结果"
    jsonschema.validate(value, schema)
    assert value.get("outcome") == original_outcome


@pytest.mark.parametrize("invalid", [None, "", " \n\t", 42, {"en": "label"}])
def test_labels_reject_null_nonstring_and_blank_values(label_contract, invalid):
    value, field, schema = label_contract
    value[field] = invalid
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, schema)
