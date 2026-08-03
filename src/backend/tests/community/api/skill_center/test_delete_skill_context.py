from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.dependencies import RequestContext
from agentclaw.community.adapters.http.skill_center.skills import delete_skill
from agentclaw.community.core.bot_management.repository.protocol import (
    BotLookupAmbiguousError,
)
from agentclaw.community.core.skill_center.services.skill_service import SkillService


BOT_ID = "20260731_yh7d4xom"
OWNER_ID = "168944"
SKILL_ID = "1120413"
ACTIVE_ROOT = Path("/home/admin/.hermes/skills")
POOL_LOCAL = Path(
    "/home/admin/.hermes/workspace/skills-pool/skills-local"
)


class _SkillServiceFactory:
    def __init__(self, *, skill_repo, device_fs) -> None:
        self._skill_repo = skill_repo
        self._device_fs = device_fs
        self.calls: list[dict] = []
        self.device_fs_calls: list[tuple[str, str]] = []

    def _device_fs_for_bot(self, bot_id: str, owner_id: str):
        self.device_fs_calls.append((bot_id, owner_id))
        return self._device_fs

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SkillService(
            skill_repo=self._skill_repo,
            skill_repo_sync=MagicMock(),
            category_repo=MagicMock(),
            market_cache=MagicMock(),
            active_dir=kwargs.get("active_dir", ACTIVE_ROOT),
            repo_dir=kwargs.get("repo_dir", ACTIVE_ROOT / "skills-repo"),
            local_dir=kwargs.get("local_dir", POOL_LOCAL),
            device_fs_factory=self._device_fs_for_bot,
            git_sync_service_factory=MagicMock(),
            local_skill_path_adapter=kwargs.get("local_skill_path_adapter"),
            runtime_uses_pool_paths=bool(kwargs.get("bot_id")),
            device_owner_id=(
                kwargs.get("bot_owner_id") or kwargs.get("entity_id")
            ),
        )


def _skill_record() -> dict:
    return {
        "id": SKILL_ID,
        "name": "find-skills",
        "git_path": f"local://{POOL_LOCAL}/find-skills",
        "bolt_id": BOT_ID,
        "user_id": OWNER_ID,
    }


def _bot_record() -> dict:
    return {
        "bot_id": BOT_ID,
        "owner_id": OWNER_ID,
        "entity_id": OWNER_ID,
        "entity_type": "staff",
        "active_engine": "hermes",
        "bot_type": "personal",
        "env": "pre",
    }


async def _call_delete(
    *,
    device_fs,
    skill_repo,
    engine_type=None,
    current_user_id=OWNER_ID,
    entity_id=None,
    bot_repo=None,
    bot_record=None,
    verified_collaborator=False,
):
    bot_repo = bot_repo or MagicMock()
    resolved_bot = bot_record or _bot_record()
    bot_repo.get_by_id_and_owner.return_value = resolved_bot
    bot_repo.get_unique_by_id.return_value = resolved_bot
    bot_repo.get_by_id_and_entity.return_value = resolved_bot

    path_factory = MagicMock()
    path_factory.get_bot_skills_dir.return_value = ACTIVE_ROOT
    path_factory.get_bot_skills_local_dir.return_value = POOL_LOCAL
    path_factory.get_bot_skills_repo_dir.return_value = (
        Path("/home/admin/.hermes/workspace/skills-pool/skills-repo")
    )

    resolver = MagicMock()
    resolver.resolve_for_bot.return_value.provider = "arca"
    edit_guard = MagicMock()
    edit_guard.acquire_for_edit.return_value = MagicMock()
    factory = _SkillServiceFactory(skill_repo=skill_repo, device_fs=device_fs)

    endpoint = getattr(delete_skill, "__wrapped__", delete_skill)
    response = await endpoint(
        skill_id=SKILL_ID,
        user_id=current_user_id,
        entity_id=entity_id,
        entity_type=None,
        bot_id=None,
        engine_type=engine_type,
        ctx=RequestContext(
            user_id=current_user_id,
            bot_id="default",
            metadata=(
                {"skill_delete_collaborator_authorized": True}
                if verified_collaborator
                else {}
            ),
        ),
        bot_repo=bot_repo,
        path_factory=path_factory,
        skill_service_factory=factory,
        resolver=resolver,
        edit_guard=edit_guard,
        skill_repo=skill_repo,
    )
    return response, path_factory, factory


@pytest.mark.asyncio
async def test_delete_without_bot_query_uses_skill_bot_and_engine_paths():
    """DELETE derives the physical context from the persisted Skill, not default."""
    skill_repo = MagicMock()
    skill_repo.get_by_id.return_value = _skill_record()
    skill_repo.list_skill_set_references.return_value = []
    skill_repo.delete.return_value = True
    device_fs = MagicMock()
    device_fs.exists = AsyncMock(return_value=True)
    device_fs.delete_tree = AsyncMock(return_value=True)

    response, path_factory, factory = await _call_delete(
        device_fs=device_fs,
        skill_repo=skill_repo,
    )

    assert response.success is True
    path_factory.get_bot_skills_dir.assert_called_once_with(
        OWNER_ID, BOT_ID, "hermes", "staff"
    )
    assert factory.calls[0]["bot_id"] == BOT_ID
    assert factory.calls[0]["engine_type"] == "hermes"
    assert device_fs.delete_tree.await_args_list[0].args[0] == (
        f"{ACTIVE_ROOT}/find-skills"
    )
    assert device_fs.delete_tree.await_args_list[1].args[0] == (
        f"{POOL_LOCAL}/find-skills"
    )


@pytest.mark.asyncio
async def test_delete_fails_closed_when_existing_active_entry_cannot_be_removed():
    """An active-link deletion failure keeps Pool source and DB intact."""
    skill_repo = MagicMock()
    skill_repo.get_by_id.return_value = _skill_record()
    skill_repo.list_skill_set_references.return_value = []
    skill_repo.delete.return_value = True
    device_fs = MagicMock()
    device_fs.exists = AsyncMock(return_value=True)
    device_fs.delete_tree = AsyncMock(return_value=False)

    with pytest.raises(HTTPException) as exc_info:
        await _call_delete(device_fs=device_fs, skill_repo=skill_repo)

    assert exc_info.value.status_code == 409
    skill_repo.delete.assert_not_called()
    device_fs.delete_tree.assert_awaited_once_with(
        f"{ACTIVE_ROOT}/find-skills"
    )


@pytest.mark.asyncio
async def test_delete_rejects_skill_referenced_by_any_skill_set():
    """Active and inactive SkillSet references both block metadata deletion."""
    skill_repo = MagicMock()
    skill_repo.get_by_id.return_value = _skill_record()
    skill_repo.list_skill_set_references.return_value = [
        {"skill_set_id": "1113652"},
        {"skill_set_id": "1113653"},
    ]
    device_fs = MagicMock()
    device_fs.exists = AsyncMock(return_value=True)
    device_fs.delete_tree = AsyncMock(return_value=True)

    with pytest.raises(HTTPException) as exc_info:
        await _call_delete(device_fs=device_fs, skill_repo=skill_repo)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "error_code": "SKILL_REFERENCED_BY_SKILL_SET",
        "message": "请先从所有技能集中移除该技能，再删除技能",
        "skill_set_ids": ["1113652", "1113653"],
    }
    skill_repo.delete.assert_not_called()
    device_fs.exists.assert_not_awaited()
    device_fs.delete_tree.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_uses_bot_owner_when_skill_was_authored_by_collaborator():
    skill_repo = MagicMock()
    skill_repo.get_by_id.return_value = {
        **_skill_record(),
        "user_id": "405935",
    }
    skill_repo.list_skill_set_references.return_value = []
    skill_repo.delete.return_value = True
    device_fs = MagicMock()
    device_fs.exists = AsyncMock(return_value=True)
    device_fs.delete_tree = AsyncMock(return_value=True)

    response, path_factory, factory = await _call_delete(
        device_fs=device_fs,
        skill_repo=skill_repo,
        current_user_id="405935",
    )

    assert response.success is True
    path_factory.get_bot_skills_dir.assert_called_once_with(
        OWNER_ID, BOT_ID, "hermes", "staff"
    )
    assert factory.device_fs_calls == [(BOT_ID, OWNER_ID)]


@pytest.mark.asyncio
async def test_authorized_collaborator_can_delete_owner_owned_skill():
    """The route passes the persisted Bot owner as trusted collaborator scope."""
    skill_repo = MagicMock()
    skill_repo.get_by_id.return_value = _skill_record()
    skill_repo.list_skill_set_references.return_value = []
    skill_repo.delete.return_value = True
    device_fs = MagicMock()
    device_fs.exists = AsyncMock(return_value=True)
    device_fs.delete_tree = AsyncMock(return_value=True)

    response, _, factory = await _call_delete(
        device_fs=device_fs,
        skill_repo=skill_repo,
        current_user_id="authorized-collaborator",
        verified_collaborator=True,
    )

    assert response.success is True
    assert factory.device_fs_calls == [(BOT_ID, OWNER_ID)]


@pytest.mark.asyncio
async def test_delete_project_bot_uses_entity_paths_and_owner_device_binding():
    skill_repo = MagicMock()
    skill_repo.get_by_id.return_value = _skill_record()
    skill_repo.list_skill_set_references.return_value = []
    skill_repo.delete.return_value = True
    bot_record = {
        **_bot_record(),
        "owner_id": OWNER_ID,
        "entity_id": "project-42",
        "entity_type": "proj",
    }
    device_fs = MagicMock()
    device_fs.exists = AsyncMock(return_value=False)

    response, path_factory, factory = await _call_delete(
        device_fs=device_fs,
        skill_repo=skill_repo,
        bot_record=bot_record,
    )

    assert response.success is True
    path_factory.get_bot_skills_dir.assert_called_once_with(
        "project-42", BOT_ID, "hermes", "proj"
    )
    assert factory.calls[0]["entity_id"] == "project-42"
    assert factory.calls[0]["bot_owner_id"] == OWNER_ID
    assert factory.device_fs_calls == [(BOT_ID, OWNER_ID)]


@pytest.mark.asyncio
async def test_delete_rejects_malformed_skill_id_with_400():
    endpoint = getattr(delete_skill, "__wrapped__", delete_skill)

    with pytest.raises(HTTPException) as exc_info:
        await endpoint(
            skill_id="not-a-number",
            user_id=OWNER_ID,
            entity_id=None,
            entity_type=None,
            bot_id=None,
            engine_type=None,
            ctx=RequestContext(user_id=OWNER_ID, bot_id="default"),
            skill_repo=MagicMock(),
            bot_repo=MagicMock(),
            path_factory=MagicMock(),
            skill_service_factory=MagicMock(),
            resolver=MagicMock(),
            edit_guard=MagicMock(),
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_delete_rejects_engine_override_that_differs_from_bot():
    skill_repo = MagicMock()
    skill_repo.get_by_id.return_value = _skill_record()
    device_fs = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await _call_delete(
            device_fs=device_fs,
            skill_repo=skill_repo,
            engine_type="openclaw",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "error_code": "SKILL_ENGINE_CONTEXT_MISMATCH",
        "message": "删除技能必须使用 Bot 当前的生效引擎",
        "active_engine": "hermes",
    }
    skill_repo.delete.assert_not_called()


@pytest.mark.asyncio
async def test_delete_uses_entity_to_disambiguate_legacy_bot_id():
    skill_repo = MagicMock()
    skill_repo.get_by_id.return_value = _skill_record()
    skill_repo.list_skill_set_references.return_value = []
    skill_repo.delete.return_value = True
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_entity.return_value = _bot_record()
    device_fs = MagicMock()
    device_fs.exists = AsyncMock(return_value=False)

    response, _, _ = await _call_delete(
        device_fs=device_fs,
        skill_repo=skill_repo,
        entity_id=OWNER_ID,
        bot_repo=bot_repo,
    )

    assert response.success is True
    bot_repo.get_by_id_and_entity.assert_called_once_with(BOT_ID, OWNER_ID)
    bot_repo.get_unique_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_delete_rejects_ambiguous_legacy_bot_without_entity():
    skill_repo = MagicMock()
    skill_repo.get_by_id.return_value = _skill_record()
    bot_repo = MagicMock()
    bot_repo.get_unique_by_id.side_effect = BotLookupAmbiguousError

    with pytest.raises(HTTPException) as exc_info:
        await _call_delete(
            device_fs=MagicMock(),
            skill_repo=skill_repo,
            bot_repo=bot_repo,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "error_code": "SKILL_BOT_CONTEXT_AMBIGUOUS",
        "message": "历史 Bot ID 不唯一，请提供 entity_id 精确定位",
        "bot_id": BOT_ID,
    }
    skill_repo.delete.assert_not_called()


@pytest.mark.asyncio
async def test_admin_can_delete_unreferenced_shared_market_skill():
    skill_repo = MagicMock()
    skill_repo.get_by_id.return_value = {
        "id": SKILL_ID,
        "name": "market-skill",
        "git_path": "git://business/market-skill",
        "bolt_id": "default",
        "user_id": None,
    }
    skill_repo.list_skill_set_references.return_value = []
    skill_repo.delete.return_value = True
    device_fs = MagicMock()
    factory = _SkillServiceFactory(skill_repo=skill_repo, device_fs=device_fs)
    endpoint = getattr(delete_skill, "__wrapped__", delete_skill)

    with patch(
        "agentclaw.community.core.skill_center.services.skill_service.skill_admin",
        return_value=[OWNER_ID],
    ):
        response = await endpoint(
            skill_id=SKILL_ID,
            user_id=OWNER_ID,
            entity_id=None,
            entity_type=None,
            bot_id=None,
            engine_type=None,
            ctx=RequestContext(user_id=OWNER_ID, bot_id="default"),
            skill_repo=skill_repo,
            bot_repo=MagicMock(),
            path_factory=MagicMock(),
            skill_service_factory=factory,
            resolver=MagicMock(),
            edit_guard=MagicMock(),
        )

    assert response.success is True
    skill_repo.delete.assert_called_once_with(SKILL_ID)
    device_fs.exists.assert_not_called()


@pytest.mark.asyncio
async def test_shared_delete_does_not_trust_query_user_id_for_admin_permission():
    skill_repo = MagicMock()
    skill_repo.get_by_id.return_value = {
        "id": SKILL_ID,
        "name": "market-skill",
        "git_path": "git://business/market-skill",
        "bolt_id": "default",
        "user_id": None,
    }
    factory = _SkillServiceFactory(skill_repo=skill_repo, device_fs=MagicMock())
    endpoint = getattr(delete_skill, "__wrapped__", delete_skill)

    with (
        patch(
            "agentclaw.community.core.skill_center.services.skill_service.skill_admin",
            return_value=[OWNER_ID],
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await endpoint(
            skill_id=SKILL_ID,
            user_id=OWNER_ID,
            entity_id=None,
            entity_type=None,
            bot_id=None,
            engine_type=None,
            ctx=RequestContext(user_id="ordinary-user", bot_id="default"),
            skill_repo=skill_repo,
            bot_repo=MagicMock(),
            path_factory=MagicMock(),
            skill_service_factory=factory,
            resolver=MagicMock(),
            edit_guard=MagicMock(),
        )

    assert exc_info.value.status_code == 403
    skill_repo.delete.assert_not_called()
