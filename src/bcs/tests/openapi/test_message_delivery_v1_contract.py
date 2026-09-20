import sys
from pathlib import Path


BCS_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = BCS_ROOT / "api-contracts" / "v1"
sys.path.insert(0, str(BCS_ROOT))

from scripts.validate_openapi_contract import load_contract, validate_contract  # noqa: E402


DELIVERY_PATHS = {
    "/openapi/v1/collaboration/messages/{message_id}/deliveries": "get",
    "/openapi/v1/collaboration/sessions/{session_id}/message-deliveries/query": "post",
    "/openapi/v1/collaboration/messages/{message_id}/deliveries/{delivery_id}/cancel": "post",
    "/openapi/v1/collaboration/messages/{message_id}/deliveries/cancel": "post",
    "/openapi/v1/collaboration/messages/{message_id}/deliveries/{delivery_id}/resolve": "post",
}


def test_typed_raw_message_delivery_contract_is_valid() -> None:
    assert validate_contract(load_contract(CONTRACT_ROOT)) == []


def test_message_delivery_operations_use_the_public_security_and_raw_wire_contract() -> None:
    contract = load_contract(CONTRACT_ROOT)

    for path, method in DELIVERY_PATHS.items():
        operation = contract["paths"][path][method]
        assert operation["x-avernet-security"] == {
            "user": "required",
            "app": "required",
        }
        assert operation["x-avernet-raw-response"] is True
        assert operation["tags"] == ["Collaboration / Sessions"]


def test_batch_delivery_query_accepts_exactly_one_bounded_selector() -> None:
    contract = load_contract(CONTRACT_ROOT)
    operation = contract["paths"][
        "/openapi/v1/collaboration/sessions/{session_id}/message-deliveries/query"
    ]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]

    assert schema["additionalProperties"] is False
    assert schema["oneOf"] == [
        {"required": ["message_ids"]},
        {"required": ["client_msg_id"]},
    ]
    assert schema["properties"]["message_ids"]["minItems"] == 1
    assert schema["properties"]["message_ids"]["maxItems"] == 100
    assert "uniqueItems" not in schema["properties"]["message_ids"]
    assert schema["properties"]["client_msg_id"]["pattern"] == ".*\\S.*"


def test_delivery_status_schema_covers_every_persisted_public_state() -> None:
    contract = load_contract(CONTRACT_ROOT)
    operation = contract["paths"][
        "/openapi/v1/collaboration/messages/{message_id}/deliveries"
    ]["get"]
    status = operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["items"]

    assert set(status["required"]) == {
        "delivery_id",
        "message_id",
        "target_bot_id",
        "flow_kind",
        "kind",
        "status",
        "state_version",
        "run_id",
        "wait_reason",
    }
    assert status["properties"]["flow_kind"]["enum"] == [
        "group",
        "direct_a2a",
        "task",
        "system",
        "state_machine",
    ]
    assert status["properties"]["kind"]["enum"] == ["send", "inject"]
    assert status["properties"]["status"]["enum"] == [
        "queued",
        "dispatching",
        "running",
        "unknown",
        "cancelling",
        "cancel_unknown",
        "completed",
        "failed",
        "cancelled",
        "expired",
        "rejected_capacity",
        "pending_context",
        "bound",
        "consumed",
        "discarded_context",
    ]


def test_manual_resolution_requires_version_reason_and_supported_evidence() -> None:
    contract = load_contract(CONTRACT_ROOT)
    operation = contract["paths"][
        "/openapi/v1/collaboration/messages/{message_id}/deliveries/{delivery_id}/resolve"
    ]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]

    assert set(schema["required"]) == {
        "session_id",
        "expected_state_version",
        "resolution",
        "reason",
    }
    assert schema["properties"]["expected_state_version"]["minimum"] == 0
    assert schema["properties"]["resolution"]["enum"] == [
        "confirmed_not_sent",
        "confirmed_stopped",
    ]
    assert schema["properties"]["reason"]["minLength"] == 1
    assert schema["properties"]["reason"]["maxLength"] == 1024
