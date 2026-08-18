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


def test_update_session_participant_accepts_bot_and_human_modes() -> None:
    contract = load_contract(CONTRACT_ROOT)
    operation = contract["paths"][
        "/openapi/v1/collaboration/sessions/{session_id}/participants/{bot_uuid}"
    ]["patch"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]

    assert schema["properties"]["mode"] == {
        "oneOf": [
            {"type": "string", "enum": ["auto", "muted"]},
            {"type": "string", "enum": ["present", "absent"]},
        ]
    }
    participant = next(
        parameter
        for parameter in operation["parameters"]
        if parameter["name"] == "bot_uuid"
    )
    assert "Bot or Human Actor identifier" in participant["description"]
    assert set(operation["responses"]["400"]["x-error-codes"]) == {
        "invalid_request",
        "invalid_participant_mode",
    }
    forbidden = operation["responses"]["403"]["description"]
    assert "only update its own present/absent mode" in forbidden
    assert "Bot participant modes require Session management authority" in forbidden


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


def _create_session_operation(contract: dict) -> dict:
    return contract["paths"][
        "/openapi/v1/collaboration/groups/{group_id}/sessions"
    ]["post"]


def test_create_group_session_uses_human_or_owned_bot_identity() -> None:
    contract = load_contract(CONTRACT_ROOT)
    operation = _create_session_operation(contract)

    assert operation["x-avernet-security"] == {
        "user": "optional",
        "app": "optional",
        "bot": "optional",
    }
    assert operation["x-bcn-identity-policy"] == "human_or_owned_bot"


def test_create_group_session_has_no_v1_reactivation_surface() -> None:
    contract = load_contract(CONTRACT_ROOT)

    assert not any("reactivat" in path for path in contract["paths"])
    operation = _create_session_operation(contract)
    schema = operation["requestBody"]["content"]["application/json"]["schema"]

    assert "session_id" not in schema["properties"]


def test_create_group_session_accepts_the_v1_native_launch_fields() -> None:
    contract = load_contract(CONTRACT_ROOT)
    operation = _create_session_operation(contract)
    schema = operation["requestBody"]["content"]["application/json"]["schema"]

    assert "required" not in schema
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {
        "title",
        "kind",
        "acting_bot_id",
        "creator_role",
        "input",
        "meta",
        "context_delivery",
    }
    assert "driver_bot_uuid" not in schema["properties"]
    assert "participants" not in schema["properties"]
    assert schema["properties"]["kind"]["enum"] == [
        "chat",
        "service_invocation",
    ]
    assert schema["properties"]["creator_role"]["enum"] == [
        "consultant",
        "manager",
        "worker",
        "observer",
    ]
    acting_creator_description = schema["properties"]["acting_bot_id"]["description"]
    for required_text in (
        "explicit creator Actor",
        "human_{user.id}",
        "Bot ID it owns",
        "Bot caller may specify only its own Bot ID",
        "When omitted",
    ):
        assert required_text in acting_creator_description
    assert schema["properties"]["context_delivery"]["enum"] == ["send", "inject"]


def test_session_input_is_a_raw_string_or_open_json_object() -> None:
    contract = load_contract(CONTRACT_ROOT)
    schema = contract["components"]["schemas"]["SessionInput"]

    assert schema["oneOf"][0] == {"type": "string"}
    object_schema = schema["oneOf"][1]
    assert object_schema["type"] == "object"
    assert object_schema["additionalProperties"] is True
    assert "properties" not in object_schema


def test_session_metadata_models_current_legacy_consumers_and_stays_open() -> None:
    contract = load_contract(CONTRACT_ROOT)
    metadata = contract["components"]["schemas"]["SessionMetadata"]

    assert metadata["type"] == "object"
    assert metadata["additionalProperties"] is True
    assert set(metadata["properties"]) == {
        "callback_target",
        "channel",
        "context_projection",
    }
    assert "payload" not in metadata["properties"]
    assert "extensions" not in metadata["properties"]

    callback = metadata["properties"]["callback_target"]
    assert callback["additionalProperties"] is True
    assert set(callback["properties"]) == {
        "baas_session_id",
        "user_id",
        "open_conversation_id",
    }

    channel = metadata["properties"]["channel"]
    assert channel["additionalProperties"] is True
    assert set(channel["properties"]) == {
        "source",
        "binding_id",
        "conversation_id",
        "conversation_type",
        "session_scope",
        "im_user_id",
        "context_projection",
    }
    assert channel["properties"]["session_scope"]["enum"] == [
        "conversation",
        "per_sender",
    ]
    assert channel["properties"]["context_projection"]["enum"] == [
        "group",
        "direct_bot",
    ]


def test_created_session_exposes_resolved_launch_and_state_machine_run() -> None:
    contract = load_contract(CONTRACT_ROOT)
    operation = _create_session_operation(contract)
    data = operation["responses"]["201"]["content"]["application/json"]["schema"][
        "properties"
    ]["data"]

    assert data["additionalProperties"] is False
    assert {"kind", "input", "meta", "participants"}.issubset(data["properties"])
    assert data["properties"]["kind"]["enum"] == ["chat", "service_invocation"]
    assert "type" not in data["properties"]["input"]
    assert "oneOf" not in data["properties"]["input"]
    assert "type" not in data["properties"]["meta"]
    assert "oneOf" not in data["properties"]["meta"]
    assert "state_machine_run_id" in data["properties"]
    run_view = data["properties"]["state_machine_run"]
    assert run_view["additionalProperties"] is False
    assert set(run_view["required"]) == {"run", "nodes"}
    assert set(run_view["properties"]) == {"run", "nodes", "judge_outputs"}


def test_create_session_documents_only_reachable_errors() -> None:
    contract = load_contract(CONTRACT_ROOT)
    responses = _create_session_operation(contract)["responses"]

    assert responses["404"]["x-error-codes"] == ["group_not_found"]
    assert responses["409"]["x-error-codes"] == ["conflict"]
