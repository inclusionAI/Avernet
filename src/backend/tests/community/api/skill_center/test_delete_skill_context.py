from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.dependencies import RequestContext
from agentclaw.community.adapters.http.skill_center.skills import delete_skill
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
            device_fs_factory=lambda _bot_id, _owner_id: self._device_fs,
            git_sync_service_factory=MagicMock(),
            local_skill_path_adapter=kwargs.get("local_skill_path_adapter"),
            runtime_uses_pool_paths=True,
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


async def _call_delete(*, device_fs, skill_repo):
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = _bot_record()

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
        user_id=OWNER_ID,
        entity_id=None,
        entity_type=None,
        bot_id=None,
        engine_type=None,
        ctx=RequestContext(user_id=OWNER_ID, bot_id="default"),
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
