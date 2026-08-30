"""Contract tests for the BCN OpenAPI V1 State Machine Run surface."""

import sys
from pathlib import Path


BCS_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = BCS_ROOT / "api-contracts" / "v1"
sys.path.insert(0, str(BCS_ROOT))

from scripts.validate_openapi_contract import load_contract, validate_contract  # noqa: E402


RERUN_PATH = "/api/v1/collaboration/state-machine-runs/{run_id}/reruns"
RUN_PATH = "/api/v1/collaboration/state-machine-runs/{run_id}"


def _contract():
    return load_contract(CONTRACT_ROOT, entrypoint="internal.yaml")


def test_rerun_operation_declares_empty_request_and_create_replay_responses() -> None:
    operation = _contract()["paths"][RERUN_PATH]["post"]

    assert "requestBody" not in operation
    assert operation["x-avernet-security"] == {"user": "required"}
    assert "failed" in operation["summary"].lower()
    assert "failed" in operation["description"].lower()
    assert {"200", "201", "400", "401", "403", "404", "409", "500"} == set(
        operation["responses"]
    )

    for status in ["200", "201"]:
        response = operation["responses"][status]["content"]["application/json"][
            "schema"
        ]
        payload = response["properties"]["data"]
        assert payload["required"] == ["run", "nodes", "idempotent_replay"]
        assert payload["properties"]["idempotent_replay"] == {"type": "boolean"}


def test_run_response_declares_optional_rerun_lineage_and_activation() -> None:
    response = _contract()["paths"][RUN_PATH]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    run = response["properties"]["data"]["properties"]["run"]

    assert run["additionalProperties"] is False
    assert "root_run_id" not in run["required"]
    assert "rerun_of" not in run["required"]
    assert "session_activation_count" not in run["required"]
    assert run["properties"]["root_run_id"] == {"type": "string", "minLength": 1}
    assert run["properties"]["rerun_of"] == {"type": "string", "minLength": 1}
    assert run["properties"]["session_activation_count"] == {
        "type": "integer",
        "minimum": 1,
    }


def test_internal_state_machine_contract_is_valid() -> None:
    assert validate_contract(
        _contract(),
        path_prefix="/api/v1/collaboration/",
    ) == []
