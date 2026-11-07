"""Exercise public dependency wiring with different Owner and entity values."""

import pytest

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import (
    CollaboratorLockService,
)
from agentclaw.community.plugin_api.passport import PassportPlugin
from tests.community.framework.device_seams import install_fake_resolver
from tests.community.endpoints.test_caller_identity_router import (
    _BOT_ID,
    _OPENAPI_HEADERS,
    _OWNER_ID,
    _enable_openapi_principal,
    _seed_owner,
)


_ENTITY = "original-owner-entity"


def _seed_transferred_bot(world, owner_id=_OWNER_ID):
    _enable_openapi_principal()
    _seed_owner(world)
    world.get(BotRepository).insert({
        "bot_id": _BOT_ID,
        "bot_name": "Transferred service Bot",
        "owner_id": owner_id,
        "entity_id": _ENTITY,
        "entity_type": "user",
        "creator_id": _ENTITY,
        "bot_type": "service",
        "status": "ACTIVE",
        "active_engine": "openclaw",
    })
    world.get(CollaboratorLockService).acquire_lock(_BOT_ID, owner_id, owner_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("locator", [
    {},
    {"owner_id": _OWNER_ID},
    {"owner_id": _ENTITY},
    {"entity_id": _ENTITY},
])
async def test_caller_context_resolves_transferred_bot(async_client, world, locator):
    _seed_transferred_bot(world)
    response = await async_client.get(
        f"/openapi/v1/bots/{_BOT_ID}/caller-context",
        params={"user_id": _OWNER_ID, "stage": "draft", **locator},
        headers=_OPENAPI_HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["editable"] is True


@pytest.mark.asyncio
async def test_explicit_entity_does_not_fall_back_to_owner(async_client, world):
    _seed_transferred_bot(world)
    response = await async_client.get(
        f"/openapi/v1/bots/{_BOT_ID}/caller-context",
        params={"user_id": _OWNER_ID, "entity_id": _OWNER_ID},
        headers=_OPENAPI_HEADERS,
    )
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_former_owner_cannot_read_using_stable_entity(async_client, world):
    _seed_transferred_bot(world, owner_id="next-owner")
    response = await async_client.get(
        f"/openapi/v1/bots/{_BOT_ID}/caller-context",
        params={"user_id": _OWNER_ID, "entity_id": _ENTITY},
        headers=_OPENAPI_HEADERS,
    )
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_cli_listing_uses_original_entity_after_handoff(async_client, world):
    _seed_transferred_bot(world)
    install_fake_resolver(world)
    world.get(PassportPlugin).set_response("query_agent_passport", {
        "mcps": [],
        "clis": [{"cli_code": "dataphin", "cli_name": "Dataphin",
                  "cli_desc": "", "identity_mode": "owner"}],
    })
    response = await async_client.get(
        f"/openapi/v1/bots/{_BOT_ID}/clis",
        params={"user_id": _OWNER_ID, "owner_id": _OWNER_ID},
        headers=_OPENAPI_HEADERS,
    )
    assert response.status_code == 200, response.text
    assert any(item["cli_code"] == "dataphin" for item in response.json()["data"])
