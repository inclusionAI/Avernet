"""Wire and schema contract for Open API message polling."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from secbaas.community.adapters.web.routers.gateway import dependencies as gateway_deps
from secbaas.community.adapters.web.routers.gateway.message_router import (
    router as gateway_router,
)
from secbaas.community.adapters.web.routers.open_api import dependencies as message_deps
from secbaas.community.adapters.web.routers.open_api.message_router import (
    router as message_router,
)
from secbaas.community.api.open_api import OpenAPICode

from .test_message_router import (
    _make_api_key_record,
    _make_context,
    _make_eval_session_log,
    _make_run_record,
)


@pytest.fixture
def polling_app():
    app = FastAPI()
    api_key = _make_api_key_record()
    api_key.policy = json.dumps({"allowed_bots": ["*"]})
    runner = MagicMock()
    runner.deliver_message = AsyncMock(return_value=("msg-001", "sess-1"))
    key_repository = MagicMock()
    key_repository.exists_bot_mapping.return_value = True
    app.dependency_overrides[message_deps.validate_api_key] = lambda: api_key
    app.dependency_overrides[message_deps.get_bot_chat_context] = _make_context
    app.dependency_overrides[gateway_deps.validate_jwt_token] = lambda: (
        gateway_deps.GatewayAuthContext("resource", 1, "t1", "gateway")
    )
    providers = {
        "bot_runner": lambda: runner,
        "resource_key_repository": lambda: key_repository,
        "eval_session_log": _make_eval_session_log,
    }
    for router in (message_router, gateway_router):
        app.include_router(router)
        for route in router.routes:
            for dependency in route.dependant.dependencies:
                if dependency.name in providers:
                    app.dependency_overrides[dependency.call] = providers[
                        dependency.name
                    ]
    with TestClient(app) as client:
        yield client, runner, api_key


@pytest.mark.parametrize(
    ("run_status", "stored_code", "expected_code"),
    [
        ("FAILED", "007", "007"),
        ("FAILED", " future/未知 ", " future/未知 "),
        ("FAILED", None, "DEFAULT"),
        ("PENDING", "unused", None),
        ("RUNNING", "unused", None),
        ("COMPLETED", "unused", None),
        ("TIME_OUT", "unused", None),
        ("ABORTED", "unused", None),
    ],
)
def test_poll_always_serializes_error_code_for_a_result(
    polling_app, run_status, stored_code, expected_code
):
    client, runner, _ = polling_app
    runner.get_result.return_value = _make_run_record(
        status=run_status, error="existing error", error_code=stored_code
    )
    response = client.get("/openapi/v1/messages/msg-001")
    assert response.status_code == 200
    body = response.json()
    assert (body["code"], body["message"]) == (0, "success")
    assert body["data"]["status"] == run_status
    assert body["data"]["error"] == "existing error"
    assert body["data"]["error_code"] == expected_code


def test_poll_does_not_expose_code_without_message_access(polling_app):
    client, runner, api_key = polling_app
    runner.get_result.return_value = _make_run_record(
        status="FAILED", error_code="private", api_key_prefix="other"
    )
    response = client.get("/openapi/v1/messages/msg-001")
    assert response.status_code == 200
    assert response.json() == {
        "code": OpenAPICode.BUSINESS_ERROR,
        "message": "Message not found: msg-001",
        "data": None,
    }
    api_key.policy = None
    response = client.get("/openapi/v1/messages/msg-001")
    assert response.status_code == 403
    assert "private" not in response.text


def test_poll_does_not_add_data_to_a_missing_record(polling_app):
    client, runner, _ = polling_app
    runner.get_result.side_effect = KeyError("msg-001")
    response = client.get("/openapi/v1/messages/msg-001")
    assert response.status_code == 404
    assert response.json() == {
        "detail": {"code": 40401, "message": "Message not found: msg-001"}
    }


def test_only_open_api_polling_changes_schema_and_wire_payload(polling_app):
    client, runner, _ = polling_app
    runner.get_result.return_value = _make_run_record(status="FAILED", error_code="007")
    gateway = client.get("/openapi/v1/chat/messages/msg-001")
    assert gateway.status_code == 200
    assert gateway.json()["data"]["status"] == "FAILED"
    assert "error_code" not in gateway.json()["data"]
    posted = client.post(
        "/openapi/v1/messages", json={"bot_id": "bot-1:entity-1", "message": "hello"}
    )
    assert posted.status_code == 200
    assert posted.json() == {
        "code": 0,
        "message": "success",
        "data": {"message_id": "msg-001", "session_id": "sess-1"},
    }

    spec = client.get("/openapi.json").json()
    components = spec["components"]["schemas"]

    def data_schema(path, method):
        ref = spec["paths"][path][method]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        data = components[ref.rsplit("/", 1)[-1]]["properties"]["data"]
        ref = next(item["$ref"] for item in data["anyOf"] if "$ref" in item)
        return components[ref.rsplit("/", 1)[-1]]

    poll = data_schema("/openapi/v1/messages/{message_id}", "get")
    assert "error_code" in poll["required"]
    assert {t["type"] for t in poll["properties"]["error_code"]["anyOf"]} == {
        "string",
        "null",
    }
    assert (
        "error_code"
        not in data_schema("/openapi/v1/chat/messages/{message_id}", "get")[
            "properties"
        ]
    )
    assert "error_code" not in data_schema("/openapi/v1/messages", "post")["properties"]
