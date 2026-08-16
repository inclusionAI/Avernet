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


def test_session_history_uses_legacy_group_message_array_envelope() -> None:
    contract = load_contract(CONTRACT_ROOT)
    operation = contract["paths"][
        "/openapi/v1/collaboration/sessions/{session_id}/messages"
    ]["get"]
    envelope = operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]

    assert envelope["additionalProperties"] is False
    data = envelope["properties"]["data"]
    assert data["type"] == "array"
    message = data["items"]
    assert message["additionalProperties"] is False
    assert set(message["required"]) == {
        "id",
        "timestamp",
        "sender",
        "content",
        "message_type",
        "role",
    }
    assert set(message["properties"]) == {
        "id",
        "timestamp",
        "sender",
        "content",
        "message_type",
        "bot_name",
        "role",
        "run_id",
        "historyMeta",
        "metadata",
        "attachments",
    }
    assert message["properties"]["message_type"]["enum"] == [
        "bot",
        "system",
        "fusion",
    ]
    assert message["properties"]["role"]["enum"] == [
        "user",
        "tool_result",
        "assistant",
        "system",
    ]
    assert message["properties"]["historyMeta"]["additionalProperties"] is True
    assert message["properties"]["metadata"]["additionalProperties"] is True
    assert set(message["properties"]["attachments"]["items"]["properties"]) == {
        "attachment_id",
        "type",
        "file_name",
        "mime_type",
        "size",
        "sha256",
        "url",
        "expires_at",
    }


def test_session_history_before_is_a_legacy_timestamp_and_old_page_types_are_absent() -> None:
    contract = load_contract(CONTRACT_ROOT)
    operation = contract["paths"][
        "/openapi/v1/collaboration/sessions/{session_id}/messages"
    ]["get"]
    before = _query_parameters(operation)["before"]

    assert before["schema"] == {
        "type": "integer",
        "format": "int64",
        "minimum": 0,
    }
    schemas = contract["components"]["schemas"]
    for old_name in [
        "SessionMessage",
        "SessionMessagePage",
        "SessionMessagePageEnvelope",
        "MessageSenderKind",
        "SessionMessageKind",
    ]:
        assert old_name not in schemas


def test_view_actor_failures_are_forbidden_without_a_bot_not_found_response() -> None:
    contract = load_contract(CONTRACT_ROOT)
    operations = [
        contract["paths"]["/openapi/v1/collaboration/groups"]["get"],
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


def test_session_collection_exposes_human_control_plane_operations() -> None:
    contract = load_contract(CONTRACT_ROOT)
    path = contract["paths"][
        "/openapi/v1/collaboration/sessions/{session_id}/collect"
    ]

    assert set(path) == {"post", "delete"}
    for operation in path.values():
        assert operation["x-avernet-security"] == {
            "user": "required",
            "app": "required",
        }
        assert {
            status: response["x-error-codes"]
            for status, response in operation["responses"].items()
            if status != "200" and "x-error-codes" in response
        } == {
            "400": ["invalid_request"],
            "401": ["unauthenticated"],
            "403": ["forbidden"],
            "404": ["session_not_found"],
            "500": ["internal_error"],
        }


def test_collect_session_requires_only_the_participant_json_field() -> None:
    contract = load_contract(CONTRACT_ROOT)
    operation = contract["paths"][
        "/openapi/v1/collaboration/sessions/{session_id}/collect"
    ]["post"]

    assert operation["operationId"] == "collect_session"
    assert operation["requestBody"]["required"] is True
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "type": "object",
        "additionalProperties": False,
        "required": ["participant"],
        "properties": {
            "participant": {"type": "string", "minLength": 1},
        },
    }


def test_uncollect_session_requires_only_the_participant_query() -> None:
    contract = load_contract(CONTRACT_ROOT)
    operation = contract["paths"][
        "/openapi/v1/collaboration/sessions/{session_id}/collect"
    ]["delete"]

    assert operation["operationId"] == "uncollect_session"
    assert operation["parameters"][1:] == [
        {
            "name": "participant",
            "in": "query",
            "required": True,
            "schema": {"type": "string", "minLength": 1},
        }
    ]


def test_session_collection_returns_a_strict_result_envelope() -> None:
    contract = load_contract(CONTRACT_ROOT)
    path = contract["paths"][
        "/openapi/v1/collaboration/sessions/{session_id}/collect"
    ]

    for operation in path.values():
        envelope = operation["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
        assert envelope["additionalProperties"] is False
        assert set(envelope["required"]) == {
            "code",
            "message",
            "data",
            "request_id",
        }
        data = envelope["properties"]["data"]
        assert data["additionalProperties"] is False
        assert set(data["required"]) == {
            "session_id",
            "participant",
            "collected",
        }
        assert set(data["properties"]) == {
            "session_id",
            "participant",
            "collected",
        }
        assert data["properties"]["collected"] == {"type": "boolean"}


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


def test_delete_session_accepts_optional_acting_bot_id_query() -> None:
    contract = load_contract(CONTRACT_ROOT)
    operation = contract["paths"][
        "/openapi/v1/collaboration/sessions/{session_id}"
    ]["delete"]
    queries = {
        parameter["name"]: parameter
        for parameter in operation["parameters"]
        if parameter["in"] == "query"
    }

    assert queries == {
        "acting_bot_id": {
            "name": "acting_bot_id",
            "in": "query",
            "required": False,
            "description": "Optional Bot identity perspective for the delete decision. Omit to evaluate the authenticated Human perspective.",
            "schema": {"type": "string", "minLength": 1},
        }
    }


def test_create_group_session_does_not_accept_driver_or_participants() -> None:
    contract = load_contract(CONTRACT_ROOT)
    operation = contract["paths"][
        "/openapi/v1/collaboration/groups/{group_id}/sessions"
    ]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]

    assert "required" not in schema
    assert set(schema["properties"]) == {"title", "input"}
    assert "driver_bot_uuid" not in schema["properties"]
    assert "participants" not in schema["properties"]
