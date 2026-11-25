"""Legacy Skill endpoints use stable resources and current personnel ACLs."""

import pytest

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillRepository,
    SkillSetRepository,
)
from agentclaw.community.utils.env_utils import get_current_env
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_collaborator


_BOT = "handoff-skill-bot"
_ENTITY = "original-owner"
_OWNER = "new-owner"


def _seed(world, *, transferred=True):
    for user in (_ENTITY, _OWNER, "member"):
        make_staff_user(world, user_id=user)
    world.get(BotRepository).insert({
        "bot_id": _BOT, "bot_name": "Handoff skills", "entity_id": _ENTITY,
        "entity_type": "staff", "owner_id": _OWNER if transferred else _ENTITY,
        "creator_id": _ENTITY, "bot_type": "service", "status": "ACTIVE",
        "active_engine": "openclaw",
    })
    skill = world.get(SkillRepository).create({
        "name": "uploaded-skill", "description": "Uploaded before or after handoff",
        "git_path": "local://uploaded-skill", "link_name": "uploaded-skill",
        "user_id": _ENTITY, "bolt_id": _BOT, "is_public": False,
    })
    sets = world.get(SkillSetRepository)
    skill_set = sets.create({
        "name": "Original set", "description": "", "user_id": _ENTITY,
        "bolt_id": _BOT, "engine_type": "openclaw", "is_default": False,
        "is_builtin": False, "is_active": True,
    })
    sets.add_mcp_to_set(
        skill_set["id"], "mcp.handoff", "Handoff MCP", user_id=_ENTITY,
        env=get_current_env(),
    )
    return skill, skill_set


@pytest.mark.asyncio
@pytest.mark.parametrize("locator", [None, _ENTITY, _OWNER])
async def test_my_skills_lists_original_assets_for_new_owner(async_client, world, locator):
    skill, _ = _seed(world)
    params = {"user_id": _OWNER, "bot_id": _BOT}
    if locator is not None:
        params["entity_id"] = locator
    response = await async_client.get(
        "/api/skills/user/my-skills", params=params, headers={"x-user-id": _OWNER}
    )
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    assert [item["id"] for item in response.json()["data"]] == [str(skill["id"])]


@pytest.mark.asyncio
async def test_set_id_and_new_owner_recovers_bot_instead_of_default(async_client, world):
    _, skill_set = _seed(world)
    response = await async_client.get(
        f"/api/skillsets/{skill_set['id']}/mcps",
        params={"user_id": _OWNER}, headers={"x-user-id": _OWNER},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["server_code"] == "mcp.handoff"


@pytest.mark.asyncio
async def test_set_hint_cannot_redirect_to_another_bot(async_client, world):
    _, skill_set = _seed(world)
    world.get(BotRepository).insert({
        "bot_id": _BOT, "bot_name": "Other Bot", "entity_id": "other-entity",
        "entity_type": "staff", "owner_id": "other-owner", "creator_id": "other-owner",
        "bot_type": "service", "status": "ACTIVE", "active_engine": "openclaw",
    })
    response = await async_client.get(
        f"/api/skillsets/{skill_set['id']}/mcps",
        params={"entity_id": "other-entity"}, headers={"x-user-id": _OWNER},
    )
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_member_can_read_set_without_person_becoming_resource_key(async_client, world):
    _, skill_set = _seed(world)
    make_collaborator(world, bot_id=_BOT, owner_id=_OWNER, user_id="member",
                      operator_id=_OWNER)
    response = await async_client.get(
        f"/api/skillsets/{skill_set['id']}/mcps",
        params={"user_id": "member", "entity_id": _ENTITY},
        headers={"x-user-id": "member"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1


@pytest.mark.asyncio
async def test_unchanged_owner_keeps_legacy_listing(async_client, world):
    _seed(world, transferred=False)
    response = await async_client.get(
        "/api/skills/user/my-skills", params={"user_id": _ENTITY, "bot_id": _BOT},
        headers={"x-user-id": _ENTITY},
    )
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["skills", "mcps"])
async def test_original_owner_loses_access_after_handoff(async_client, world, resource):
    _, skill_set = _seed(world)
    if resource == "skills":
        path = "/api/skills/user/my-skills"
        params = {"user_id": _ENTITY, "bot_id": _BOT, "entity_id": _ENTITY}
    else:
        path = f"/api/skillsets/{skill_set['id']}/mcps"
        params = {"user_id": _ENTITY}
    response = await async_client.get(path, params=params, headers={"x-user-id": _ENTITY})
    assert response.status_code in {403, 404} or response.json().get("success") is False


@pytest.mark.asyncio
async def test_personal_default_stays_owner_qualified(async_client, world):
    _seed(world)
    for owner in (_ENTITY, _OWNER):
        world.get(BotRepository).insert({
            "bot_id": "default", "bot_name": "Personal", "entity_id": owner,
            "entity_type": "staff", "owner_id": owner, "creator_id": owner,
            "bot_type": "personal", "status": "ACTIVE", "active_engine": "openclaw",
        })
        world.get(SkillRepository).create({
            "name": f"personal-{owner}", "description": "Personal upload",
            "git_path": f"local://personal-{owner}", "link_name": f"personal-{owner}",
            "user_id": owner, "bolt_id": "default", "is_public": False,
        })
    response = await async_client.get(
        "/api/skills/user/my-skills", params={"user_id": _OWNER, "bot_id": "default"},
        headers={"x-user-id": _OWNER},
    )
    assert response.status_code == 200, response.text
    assert [item["name"] for item in response.json()["data"]] == [f"personal-{_OWNER}"]


@pytest.mark.asyncio
async def test_detached_self_catalog_retains_legacy_read(async_client, world):
    _seed(world)
    world.get(SkillRepository).create({
        "name": "detached", "description": "Historical asset",
        "git_path": "local://detached", "link_name": "detached",
        "user_id": _OWNER, "bolt_id": "archived-bot", "is_public": False,
    })
    response = await async_client.get(
        "/api/skills/user/my-skills", params={"user_id": _OWNER, "bot_id": "archived-bot"},
        headers={"x-user-id": _OWNER},
    )
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1
    denied = await async_client.get(
        "/api/skills/user/my-skills", params={"user_id": _OWNER, "bot_id": "archived-bot"},
        headers={"x-user-id": "member"},
    )
    assert denied.status_code in {403, 404} or denied.json().get("success") is False
