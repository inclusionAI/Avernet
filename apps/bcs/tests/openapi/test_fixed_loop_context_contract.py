"""Pending Human Loop context is complete when present and absent for legacy nodes."""

import sys
from pathlib import Path

import jsonschema
import pytest

BCS_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BCS_ROOT))

from scripts.validate_openapi_contract import load_contract  # noqa: E402


@pytest.fixture(scope="module")
def schemas():
    return load_contract(
        BCS_ROOT / "api-contracts" / "v1" / "openapi",
        entrypoint="state-machine-runs.yaml",
    )


def context(iteration=2):
    return {
        "loop_id": "rounds", "iteration": iteration, "max_iterations": 3,
        "previous_result": None if iteration == 1 else {
            "iteration": iteration - 1, "result_node_id": "decision",
            "execution_node_id": "ln-previous-result", "outcome": "continue",
            "output": "Previous result", "completed_at": 1234,
        },
    }


def pending():
    return {
        "node_id": "ln-entry", "display_name": "Review", "instruction": "Review the result",
        "response_ref": "ref-2", "judge_outcomes": [], "upstream_artifacts": [],
    }


@pytest.mark.parametrize("iteration", [1, 2, 3])
def test_pending_context_accepts_complete_first_and_later_iterations(schemas, iteration):
    node = pending()
    node["loop_context"] = context(iteration)
    jsonschema.validate(node, schemas["PendingHumanNodeView"])


def test_legacy_pending_node_omits_context(schemas):
    jsonschema.validate(pending(), schemas["PendingHumanNodeView"])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**pending(), "loop_context": None}, schemas["PendingHumanNodeView"])


@pytest.mark.parametrize("missing", ["loop_id", "iteration", "max_iterations", "previous_result"])
def test_present_context_requires_every_field(schemas, missing):
    value = context()
    del value[missing]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, schemas["LoopContext"])


@pytest.mark.parametrize("missing", ["iteration", "result_node_id", "execution_node_id", "outcome", "output", "completed_at"])
def test_previous_result_requires_every_field(schemas, missing):
    value = context()
    del value["previous_result"][missing]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, schemas["LoopContext"])


@pytest.mark.parametrize("iteration", [1, 2])
def test_only_first_iteration_has_null_previous_result(schemas, iteration):
    value = context(iteration)
    value["previous_result"] = context(2 if iteration == 1 else 1)["previous_result"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, schemas["LoopContext"])
