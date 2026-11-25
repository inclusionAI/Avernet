"""Legacy locators must be resolved before invoking entity-only services."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.bot_address import resolve_bot_entity
from agentclaw.community.adapters.http.openapi_v1.errors import BotAccessRefusedError
from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError


def _world(query: bytes = b""):
    bot = {"id": 7, "bot_id": "service-bot", "entity_id": "original-entity",
           "owner_id": "new-owner"}
    repository = Mock()
    repository.list_matches_by_legacy_locator.side_effect = (
        lambda bot_id, locator: [bot]
        if bot_id == bot["bot_id"] and locator in {bot["entity_id"], bot["owner_id"]}
        else []
    )
    repository.get_by_id_and_entity.side_effect = (
        lambda bot_id, entity: bot if entity == bot["entity_id"] else None
    )
    app = FastAPI()
    app.state.injector = SimpleNamespace(get=lambda _contract: repository)
    request = Request({"type": "http", "method": "GET", "path": "/",
                       "app": app, "query_string": query})
    return request, repository


@pytest.mark.asyncio
@pytest.mark.parametrize("locator", ["new-owner", "original-entity"])
async def test_legacy_owner_resolves_to_immutable_entity(locator):
    request, repository = _world()
    assert await resolve_bot_entity(
        request, bot_id="service-bot", locator=locator
    ) == "original-entity"
    repository.list_matches_by_legacy_locator.assert_called_once_with("service-bot", locator)


@pytest.mark.asyncio
async def test_explicit_entity_is_strict_and_never_falls_back_to_current_owner():
    request, repository = _world(b"entity_id=new-owner")
    with pytest.raises(BotAccessRefusedError):
        await resolve_bot_entity(request, bot_id="service-bot", locator="new-owner")
    repository.list_matches_by_legacy_locator.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_stable_entity_resolves():
    request, repository = _world(b"entity_id=original-entity")
    assert await resolve_bot_entity(
        request, bot_id="service-bot", locator="original-entity"
    ) == "original-entity"
    repository.get_by_id_and_entity.assert_called_once_with("service-bot", "original-entity")


@pytest.mark.asyncio
async def test_ambiguous_legacy_locator_is_rejected():
    request, repository = _world()
    repository.list_matches_by_legacy_locator.side_effect = None
    repository.list_matches_by_legacy_locator.return_value = [{"id": 7}, {"id": 8}]
    with pytest.raises(BotLookupAmbiguousError):
        await resolve_bot_entity(request, bot_id="service-bot", locator="new-owner")


@pytest.mark.asyncio
async def test_missing_legacy_locator_is_masked():
    request, _repository = _world()
    with pytest.raises(BotAccessRefusedError):
        await resolve_bot_entity(request, bot_id="service-bot", locator="absent")
