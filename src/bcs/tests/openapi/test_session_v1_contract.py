import sys
from pathlib import Path

BCS_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = BCS_ROOT / "api-contracts" / "v1"
sys.path.insert(0, str(BCS_ROOT))

from scripts.validate_openapi_contract import load_contract  # noqa: E402


def _query_parameters(operation: dict) -> dict[str, dict]:
    return {
        parameter["name"]: parameter
        for parameter in operation["parameters"]
        if parameter["in"] == "query"
    }


def test_session_list_and_history_use_the_shared_view_actor_contract() -> None:
    contract = load_contract(CONTRACT_ROOT)
    list_sessions = contract["paths"][
        "/openapi/v1/collaboration/groups/{group_id}/sessions"
    ]["get"]
    list_messages = contract["paths"][
        "/openapi/v1/collaboration/sessions/{session_id}/messages"
    ]["get"]

    list_queries = _query_parameters(list_sessions)
    message_queries = _query_parameters(list_messages)

    assert set(list_queries) == {"offset", "limit", "status", "view_bot_id"}
    assert set(message_queries) == {"before", "limit", "view_bot_id"}
    assert list_queries["view_bot_id"] == message_queries["view_bot_id"]
    assert list_queries["view_bot_id"]["schema"] == {
        "type": "string",
        "minLength": 1,
    }
    assert list_queries["view_bot_id"].get("required", False) is False


def test_view_actor_failures_are_forbidden_without_a_bot_not_found_response() -> None:
    contract = load_contract(CONTRACT_ROOT)
    operations = [
        contract["paths"]["/openapi/v1/collaboration/bots/{bot_id}/groups"]["get"],
        contract["paths"][
            "/openapi/v1/collaboration/groups/{group_id}/sessions"
        ]["get"],
        contract["paths"][
            "/openapi/v1/collaboration/sessions/{session_id}/messages"
        ]["get"],
    ]

    for operation in operations:
        assert operation["responses"]["403"]["x-error-codes"] == ["forbidden"]
        assert "bot_not_found" not in {
            error_code
            for response in operation["responses"].values()
            for error_code in response.get("x-error-codes", [])
        }


def test_session_detail_uses_implicit_human_or_owned_bot_participant_access() -> None:
    contract = load_contract(CONTRACT_ROOT)
    operation = contract["paths"][
        "/openapi/v1/collaboration/sessions/{session_id}"
    ]["get"]

    assert "view_bot_id" not in {
        parameter["name"]
        for parameter in operation["parameters"]
        if parameter["in"] == "query"
    }
    forbidden = operation["responses"]["403"]
    assert forbidden["x-error-codes"] == ["forbidden"]
    description = forbidden["description"]
    assert "Human Actor" in description
    assert "created by that Human" in description
    assert "Session Participant" in description


def test_session_completion_endpoint_is_not_in_public_contract() -> None:
    contract = load_contract(CONTRACT_ROOT)

    assert (
        "/openapi/v1/collaboration/sessions/{session_id}/completion"
        not in contract["paths"]
    )


def test_add_session_participant_accepts_only_bot_uuid() -> None:
    contract = load_contract(CONTRACT_ROOT)
    operation = contract["paths"][
        "/openapi/v1/collaboration/sessions/{session_id}/participants"
    ]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]

    assert schema == {
        "type": "object",
        "additionalProperties": False,
        "required": ["bot_uuid"],
        "properties": {"bot_uuid": {"type": "string"}},
    }
