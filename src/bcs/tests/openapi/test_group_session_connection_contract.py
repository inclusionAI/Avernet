import sys
from pathlib import Path


BCS_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = BCS_ROOT / "api-contracts" / "v1"
sys.path.insert(0, str(BCS_ROOT))

from scripts.validate_openapi_contract import (  # noqa: E402
    load_contract,
    validate_contract,
)


TOKEN_PATH = "/openapi/v1/collaboration/sessions/{session_id}/token"
WEBSOCKET_PATH = "/openapi/v1/collaboration/messages/ws"


def _contract() -> dict:
    return load_contract(CONTRACT_ROOT)


def test_token_issuance_contract_matches_the_existing_http_delivery_slice() -> None:
    operation = _contract()["paths"][TOKEN_PATH]["post"]

    assert operation["operationId"] == "issue_group_session_connection_token"
    assert operation["x-avernet-security"] == {"user": "required"}
    assert "requestBody" not in operation
    assert operation["parameters"] == [
        {
            "name": "session_id",
            "in": "path",
            "required": True,
            "schema": {"type": "string", "minLength": 1},
        }
    ]

    responses = operation["responses"]
    assert set(responses) == {"200", "401", "403", "404", "500"}
    success = responses["200"]
    assert set(success["headers"]) == {"Cache-Control", "Pragma"}
    assert success["headers"]["Cache-Control"]["schema"] == {
        "type": "string",
        "const": "no-store",
    }
    assert success["headers"]["Pragma"]["schema"] == {
        "type": "string",
        "const": "no-cache",
    }

    envelope = success["content"]["application/json"]["schema"]
    assert envelope["additionalProperties"] is False
    assert set(envelope["required"]) == {"code", "message", "data", "request_id"}
    data = envelope["properties"]["data"]
    assert data["additionalProperties"] is False
    assert set(data["required"]) == {"token", "expires_at"}
    assert data["properties"] == {
        "token": {"type": "string", "minLength": 1, "maxLength": 4096},
        "expires_at": {"type": "integer", "format": "int64", "minimum": 1},
    }

    assert responses["401"]["x-error-codes"] == ["unauthenticated"]
    assert responses["403"]["x-error-codes"] == ["forbidden"]
    assert responses["404"]["x-error-codes"] == ["session_not_found"]
    assert responses["500"]["x-error-codes"] == ["internal_error"]


def test_websocket_contract_describes_only_upgrade_and_authentication() -> None:
    operation = _contract()["paths"][WEBSOCKET_PATH]["get"]

    assert operation["operationId"] == "connect_group_session_websocket"
    assert operation["x-avernet-protocol"] == "websocket"
    assert operation["x-avernet-security"] == {}
    assert "requestBody" not in operation
    assert operation["parameters"] == [
        {
            "name": "token",
            "in": "query",
            "required": True,
            "description": operation["parameters"][0]["description"],
            "x-sensitive": True,
            "schema": {"type": "string", "minLength": 1, "maxLength": 4096},
        }
    ]
    assert "credential" in operation["parameters"][0]["description"].lower()

    responses = operation["responses"]
    assert set(responses) == {"101", "401", "503"}
    assert "content" not in responses["101"]
    assert responses["401"]["x-error-codes"] == ["invalid_connection_token"]
    assert responses["503"]["x-error-codes"] == ["token_service_unavailable"]
    assert "messages" not in operation


def test_validator_accepts_the_http_upgrade_success_response() -> None:
    assert validate_contract(_contract()) == []
