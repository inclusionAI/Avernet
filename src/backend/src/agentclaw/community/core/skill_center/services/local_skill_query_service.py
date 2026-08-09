"""Desired-state Local Skill queries for the public API.

This service deliberately has no device or runtime dependency: database desired
state remains readable while a Bot is offline.
"""

from __future__ import annotations

from typing import Any

from injector import inject

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    LocalSkillOwnerAmbiguousError,
)
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository


class LocalSkillQueryService:
    """Authorize a Bot scope then project only exact ``local://`` rows."""

    @inject
    def __init__(
        self,
        skill_repo: SkillRepository,
        bot_repo: BotRepository,
        collaborator_service: CollaboratorServiceProtocol,
    ) -> None:
        self._skill_repo = skill_repo
        self._bot_repo = bot_repo
        self._collaborator_service = collaborator_service

    def list_local_skills(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        page: int,
        page_size: int,
        active: bool | None,
        keyword: str | None,
    ) -> tuple[int, list[dict[str, Any]]]:
        self._require_view_access(bot_id=bot_id, owner_id=owner_id, actor_id=actor_id)
        return self._skill_repo.list_bot_local_skills(
            bot_id=bot_id,
            user_id=owner_id,
            page=page,
            page_size=page_size,
            active=active,
            keyword=keyword,
        )

    def get_local_skill(self, *, skill_id: str, actor_id: str) -> dict[str, Any]:
        if not skill_id.isdecimal():
            raise LocalSkillNotFoundError()
        skill = self._skill_repo.get_by_id(skill_id)
        if not self._is_exact_local_skill(skill):
            if self._is_unresolvable_legacy_local_skill(skill):
                raise LocalSkillOwnerAmbiguousError()
            raise LocalSkillNotFoundError()
        owner_id = str(skill["user_id"])
        bot_id = str(skill["bolt_id"])
        self._require_view_access(bot_id=bot_id, owner_id=owner_id, actor_id=actor_id)
        return (
            self._skill_repo.get_bot_local_skill(
                skill_id=skill_id, bot_id=bot_id, user_id=owner_id
            )
            or self._not_found()
        )

    @staticmethod
    def _is_exact_local_skill(skill: dict[str, Any] | None) -> bool:
        return bool(
            skill
            and skill.get("user_id")
            and skill.get("bolt_id")
            and str(skill.get("git_path") or "").startswith("local://")
        )

    @staticmethod
    def _is_unresolvable_legacy_local_skill(skill: dict[str, Any] | None) -> bool:
        """Whether a legacy default Local row lacks its trusted owner field."""
        return bool(
            skill
            and skill.get("bolt_id") == "default"
            and not skill.get("user_id")
            and str(skill.get("git_path") or "").startswith("local://")
        )

    def _require_view_access(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> None:
        if self._bot_repo.get_by_id_and_owner(bot_id, owner_id) is None:
            raise LocalSkillNotFoundError()
        if actor_id == owner_id:
            return
        permission = self._collaborator_service.check_collaborator_permission(
            bot_id, owner_id, actor_id, PermissionLevel.MEMBER
        )
        if not permission.get("has_permission"):
            raise LocalSkillNotFoundError()

    @staticmethod
    def _not_found() -> dict[str, Any]:
        raise LocalSkillNotFoundError()
