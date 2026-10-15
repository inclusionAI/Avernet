"""Classification through the signed Gateway principal and public envelope."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.openapi_v1.bots.classification import router
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    PRINCIPAL_HEADER,
    require_principal,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
)
from tests.community.adapters.http.openapi_v1.test_principal_seam import (
    mint,
    signing_key,  # noqa: F401 — installs/resets the real verifier per test
)

_PATH = "/openapi/v1/bots/bot-1/classification"


@pytest.fixture
def classification_client():
    service = MagicMock(spec=BotServiceProtocol)
    service.get_bot_classification.return_value = {
        "bot_id": "bot-1",
        "bot_type": "service",
        "owner_id": "someone-else",
        "template_config": {"private": "hidden"},
    }
    app = mount_public_error_handlers(FastAPI())
    app.include_router(router, dependencies=[Depends(require_principal)])
    injector = Injector()
    injector.binder.bind(BotServiceProtocol, to=service)
    attach_injector(app, injector)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, service


@pytest.mark.parametrize("bot_type", ["service", "personal", "desktop"])
def test_authenticated_non_owner_receives_only_minimal_classification(
    classification_client, bot_type
):
    client, service = classification_client
    service.get_bot_classification.return_value["bot_type"] = bot_type
    response = client.get(_PATH, headers={PRINCIPAL_HEADER: mint()})
    assert response.status_code == 200
    assert response.json()["code"] == 200000
    assert response.json()["data"] == {"bot_id": "bot-1", "bot_type": bot_type}
    service.get_bot_classification.assert_called_once_with("bot-1", entity_id=None)


def test_composite_identity_entity_is_forwarded(classification_client):
    client, service = classification_client
    response = client.get(
        _PATH,
        params={"entity_id": "owner-2"},
        headers={PRINCIPAL_HEADER: mint()},
    )
    assert response.status_code == 200
    service.get_bot_classification.assert_called_once_with("bot-1", entity_id="owner-2")


@pytest.mark.parametrize("app_only", [False, True])
def test_missing_human_identity_is_rejected_before_service(
    classification_client, app_only
):
    client, service = classification_client
    headers = {PRINCIPAL_HEADER: mint(user_id=None)} if app_only else {}
    response = client.get(_PATH, headers=headers)
    assert response.status_code == 401
    assert response.json()["code"] == 401000
    service.get_bot_classification.assert_not_called()


def test_not_found_uses_public_error_envelope(classification_client):
    client, service = classification_client
    service.get_bot_classification.return_value = None
    response = client.get(_PATH, headers={PRINCIPAL_HEADER: mint()})
    assert response.status_code == 404
    assert response.json()["code"] == 404000
    assert response.json()["data"] is None


def test_ambiguous_lookup_requires_entity_id(classification_client):
    client, service = classification_client
    service.get_bot_classification.side_effect = BotLookupAmbiguousError(
        "private detail"
    )
    response = client.get(_PATH, headers={PRINCIPAL_HEADER: mint()})
    assert response.status_code == 409
    assert response.json()["code"] == 409000
    assert "entity_id" in response.json()["message"]
    assert "private detail" not in response.text


def test_empty_entity_is_rejected(classification_client):
    client, service = classification_client
    response = client.get(
        _PATH, params={"entity_id": ""}, headers={PRINCIPAL_HEADER: mint()}
    )
    assert response.status_code == 422
    service.get_bot_classification.assert_not_called()
