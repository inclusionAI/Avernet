"""Caller status through the real signed principal and chat access policy."""
from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.openapi_v1.bots.router import router
from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER, require_principal
from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.api.bot_app_grant_service import BotAppGrantServiceProtocol
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.service_bot.baas_service_errors import BaasServiceError
from tests.community.adapters.http.openapi_v1.conftest import mount_public_error_handlers, public_document, SeamBots
from tests.community.adapters.http.openapi_v1.test_principal_seam import mint, signing_key  # noqa: F401
from tests.community.core.expert_chat.services.test_caller_instance_status import authorized_service, BOT_ID, OWNER_ID, USER_ID

PATH = f"/openapi/v1/bots/{BOT_ID}/caller-instance/status"


@pytest.fixture
def status_client():
    service = authorized_service()
    grants = MagicMock()
    grants.find.return_value = MagicMock()
    app = mount_public_error_handlers(FastAPI())
    app.include_router(router, dependencies=[Depends(require_principal)])
    injector = Injector()
    injector.binder.bind(ExpertChatServiceProtocol, to=service)
    injector.binder.bind(BotAppGrantServiceProtocol, to=grants)
    injector.binder.bind(BotRepository, to=SeamBots())
    attach_injector(app, injector)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, service, grants


def read(client, *, user=USER_ID, principal_user=USER_ID, include_app=False, **kwargs):
    return client.get(PATH, params={"user_id": user, "entity_id": OWNER_ID},
        headers={PRINCIPAL_HEADER: mint(user_id=principal_user, include_app=include_app)}, **kwargs)


def test_public_noncollaborator_gets_only_two_booleans(status_client, caplog):
    client, service, _ = status_client
    with caplog.at_level("INFO"):
        response = read(client)
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"exists": True, "alive": False}
    service._collaborator_service.check_collaborator_permission.assert_not_called()
    assert "event=caller_instance_status.request" in caplog.text
    assert "event=caller_instance_status.success" in caplog.text


def test_private_noncollaborator_is_masked(status_client):
    client, service, _ = status_client
    service._find_chat_bot.return_value["public"] = "0"
    response = read(client)
    assert response.status_code == 404
    service._instance_service.get_caller_instance_status.assert_not_awaited()


def test_missing_bot_is_masked(status_client):
    client, service, _ = status_client
    service._find_chat_bot.return_value = None
    assert read(client).status_code == 404
    service._instance_service.get_caller_instance_status.assert_not_awaited()


@pytest.mark.parametrize("include_app", [False, True])
def test_human_and_mixed_principals_cannot_select_other_user(status_client, include_app):
    client, service, _ = status_client
    response = read(client, user="different", include_app=include_app)
    assert response.status_code == 403
    service._find_chat_bot.assert_not_called()


def test_missing_principal_and_required_user(status_client):
    client, service, _ = status_client
    assert client.get(PATH, params={"user_id": USER_ID}).status_code == 401
    assert client.get(PATH, headers={PRINCIPAL_HEADER: mint(user_id=USER_ID)}).status_code == 422
    service._find_chat_bot.assert_not_called()


def test_application_only_requires_addressed_grant(status_client):
    client, service, grants = status_client
    assert read(client, principal_user=None, include_app=True).status_code == 200
    args = grants.find.call_args.kwargs
    assert args["bot_id"] == BOT_ID and args["owner_id"] == OWNER_ID and args["user_id"] == USER_ID
    service._find_chat_bot.reset_mock()
    grants.find.return_value = None
    assert read(client, principal_user=None, include_app=True).status_code == 404
    service._find_chat_bot.assert_not_called()


def test_upstream_error_is_safe_502(status_client, caplog):
    client, service, _ = status_client
    service._instance_service.get_caller_instance_status.side_effect = BaasServiceError("sentinel-credential")
    with caplog.at_level("INFO"):
        response = read(client)
    assert response.status_code == 502
    assert "sentinel-credential" not in response.text + caplog.text
    assert "event=caller_instance_status.failed" in caplog.text
    assert "event=caller_instance_status.success" not in caplog.text


def test_owner_alias_remains_accepted(status_client):
    client, service, _ = status_client
    response = client.get(PATH, params={"user_id": USER_ID, "owner_id": OWNER_ID}, headers={PRINCIPAL_HEADER: mint(user_id=USER_ID)})
    assert response.status_code == 200
    service._find_chat_bot.assert_called_once_with(BOT_ID, OWNER_ID)


def test_public_schema_has_exact_required_boolean_fields():
    document = public_document()
    operation = document["paths"]["/openapi/v1/bots/{bot_id}/caller-instance/status"]["get"]
    assert operation["responses"]["200"]
    schema = document["components"]["schemas"]["CallerInstanceStatus"]
    assert set(schema["required"]) == {"exists", "alive"}
    assert set(schema["properties"]) == {"exists", "alive"}
    assert all(field["type"] == "boolean" for field in schema["properties"].values())
