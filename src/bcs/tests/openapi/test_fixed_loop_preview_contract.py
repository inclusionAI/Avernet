"""Fixed Loop preview schema compatibility and routing semantics."""

import json
import sys
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest

BCS_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BCS_ROOT))

from scripts.validate_openapi_contract import load_contract  # noqa: E402


def schemas():
    return load_contract(
        BCS_ROOT / "api-contracts" / "v1" / "openapi",
        entrypoint="collaboration-definitions.yaml",
    )


def test_frontend_preview_fixture_matches_the_public_validation_contract():
    response = json.loads((BCS_ROOT / "tests/fixtures/fixed_loop_preview.json").read_text())
    jsonschema.validate(response, schemas()["CollaborationDefinitionValidationOutcome"])
    graph = response["graph"]
    assert graph["graph_mode"] == "hierarchical"
    assert graph["execution_graph_mode"] == "acyclic"
    assert {edge["loop_route"]["kind"] for edge in graph["edges"]} == {"continue", "break", "exhausted"}
    assert response["warnings"][0]["code"] == "VALIDATION_ONLY_FEATURE"


def exhausted_preview():
    return {
        "graph_mode": "hierarchical",
        "execution_graph_mode": "acyclic",
        "nodes": [
            {
                "node_id": "ln-last-result",
                "display_name": "Decide",
                "kind": "bot_task",
                "final_output": False,
                "judge": True,
                "execution": {
                    "definition_node_id": "decision",
                    "loop_id": "rounds",
                    "iteration": 3,
                    "max_iterations": 3,
                },
            }
        ],
        "edges": [
            {
                "source": "ln-last-result",
                "outcome": "continue",
                "target": "manual_finish",
                "loop_route": {"kind": "exhausted", "logical_outcome": "exhausted"},
            }
        ],
    }


def test_preview_accepts_real_outcome_and_exhausted_logical_route():
    contract = schemas()
    preview = exhausted_preview()
    jsonschema.validate(preview, contract["CollaborationDefinitionGraphPreview"])
    edge = preview["edges"][0]
    assert edge["outcome"] != edge["loop_route"]["logical_outcome"]
    assert contract["CollaborationDefinitionGraphEdge"]["properties"]["loop_route"] == contract["StateMachineLoopRoute"]


def test_v1_preview_omits_additive_fields_and_retains_existing_shape():
    preview = exhausted_preview()
    preview["graph_mode"] = "acyclic"
    del preview["execution_graph_mode"]
    del preview["nodes"][0]["execution"]
    del preview["edges"][0]["loop_route"]
    jsonschema.validate(preview, schemas()["CollaborationDefinitionGraphPreview"])


@pytest.mark.parametrize("missing", ["definition_node_id", "loop_id", "iteration", "max_iterations"])
def test_execution_metadata_requires_all_fields_when_present(missing):
    preview = exhausted_preview()
    del preview["nodes"][0]["execution"][missing]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(preview, schemas()["CollaborationDefinitionGraphPreview"])


@pytest.mark.parametrize("route", [{}, {"kind": "exhausted"}, {"kind": "unknown", "logical_outcome": "x"}, {"kind": "break", "logical_outcome": ""}])
def test_route_requires_valid_kind_and_nonempty_logical_outcome(route):
    preview = deepcopy(exhausted_preview())
    preview["edges"][0]["loop_route"] = route
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(preview, schemas()["CollaborationDefinitionGraphPreview"])
