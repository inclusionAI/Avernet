"""Catalog stage contracts remain optional, scoped and display-safe."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.bots import router
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_inventory.protocols import BotInventoryTemplatePort, BusinessSpaceContextProtocol
from agentclaw.community.core.repository.protocols.bot import BotRepository
from tests.community.adapters.http.openapi_v1.conftest import user_scoped_client
from tests.community.adapters.http.openapi_v1.test_collaborating_bots import _client, _relation


@pytest.fixture
def catalog_client():
    service = MagicMock()
    repository = MagicMock()
    templates = MagicMock()
    spaces = MagicMock()
    spaces.bot_space.return_value = None
    service.list_bots_by_conditions.return_value = {"total": 0, "items": []}
    repository.list_by_conditions.return_value = (0, [])
    templates.list_template_configs_by_bot_ids.return_value = {}

    class Ports(Module):
        def configure(self, binder):
            binder.bind(BotServiceProtocol, to=service)
            binder.bind(BotRepository, to=repository)
            binder.bind(BotInventoryTemplatePort, to=templates)
            binder.bind(BusinessSpaceContextProtocol, to=spaces)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "owner"}
    attach_injector(app, Injector([Ports()]))
    return SimpleNamespace(
        client=user_scoped_client(app, "owner"), repository=repository,
        service=service, templates=templates,
    )


@pytest.mark.parametrize("stage", ["draft", "verify", "online"])
def test_owned_stage_listing_pushes_all_filters_before_pagination(catalog_client, stage):
    response = catalog_client.client.get("/openapi/v1/bots", params={
        "stage": stage, "keyword": "Bot", "engine": "openclaw",
        "status": "ACTIVE", "page": 2, "page_size": 7,
    })
    assert response.status_code == 200, response.json()
    catalog_client.repository.list_by_conditions.assert_called_once_with(
        owner_id="owner", bot_name="Bot", engine="openclaw", status="ACTIVE",
        page=2, page_size=7, bot_ids=None, stage=stage,
    )
    catalog_client.service.list_bots_by_conditions.assert_not_called()


def test_omitted_stage_defaults_to_draft(catalog_client):
    assert catalog_client.client.get("/openapi/v1/bots").status_code == 200
    assert catalog_client.repository.list_by_conditions.call_args.kwargs["stage"] == "draft"
    catalog_client.service.list_bots_by_conditions.assert_not_called()


def test_invalid_stage_never_queries_repository(catalog_client):
    assert catalog_client.client.get("/openapi/v1/bots?stage=eval").status_code == 422
    catalog_client.repository.list_by_conditions.assert_not_called()


def test_collaboration_online_filter_reaches_scoped_repository():
    bots, collaborators = MagicMock(), MagicMock()
    bots.list_bots_by_owner_bot_pairs.return_value = {"total": 0, "items": []}
    collaborators.list_user_collaborations.return_value = [_relation()]
    response = _client(bots=bots, collaborators=collaborators).get(
        "/openapi/v1/bots/collaborations?stage=online&page=2&page_size=7",
    )
    assert response.status_code == 200
    bots.page_live_bots_by_pks.assert_called_once_with(
        bot_pks=[91], exclude_owner_id="collab-1", entity_pairs=None,
        page=2, page_size=7, stage="online",
    )


def test_metadata_projection_has_stage_information_but_no_runtime_internals(catalog_client):
    catalog_client.service.list_bots_by_owner_bot_pairs.return_value = {"total": 1, "items": [{
        "bot_id": "b1", "owner_id": "other", "bot_type": "service", "status": "ACTIVE",
        "online_available": True, "live_version": 3, "conversation_stage": "online",
        "binding_id": 71, "ext": {"binding": {"online": 71}},
    }]}
    response = catalog_client.client.post("/openapi/v1/bots/metadata/queries", json={
        "bots": [{"bot_id": "b1", "owner_id": "other"}],
    })
    assert response.status_code == 200
    row = response.json()["data"]["items"][0]
    assert row["online_available"] is True
    assert row["live_version"] == 3
    assert row["conversation_stage"] == "online"
    assert "binding_id" not in row and "ext" not in row
