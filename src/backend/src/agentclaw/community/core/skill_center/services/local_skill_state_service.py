"""Desired-state activation commands for Bot-owned Local Skills."""

from __future__ import annotations

from typing import Any

from injector import inject

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.skill_center.errors import (
    LocalSkillEditPausedError,
    LocalSkillNotFoundError,
    LocalSkillNotReadyError,
    LocalSkillRuntimeSyncError,
    LocalSkillStorageError,
)
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.skill_center.services.repositories import (
    SkillRepository,
    SkillSetRepository,
)
from agentclaw.community.core.skills_pool.edit_guard import (
    SkillsPoolEditGuard,
    SkillsPoolEditPausedError,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope


class LocalSkillStateService:
    """Authorize, mutate desired state, then synchronously reconcile runtime."""

    @inject
    def __init__(
        self,
        skill_repo: SkillRepository,
        skill_set_repo: SkillSetRepository,
        bot_repo: BotRepository,
        collaborator_service: CollaboratorServiceProtocol,
        skill_set_service_factory: SkillSetServiceFactory,
        edit_guard: SkillsPoolEditGuard,
    ) -> None:
        self._skill_repo = skill_repo
        self._skill_set_repo = skill_set_repo
        self._bot_repo = bot_repo
        self._collaborators = collaborator_service
        self._skill_set_service_factory = skill_set_service_factory
        self._edit_guard = edit_guard

    async def set_local_skill_active(
        self, *, skill_id: str, actor_id: str, active: bool
    ) -> dict[str, Any]:
        scope = self._discover_scope(skill_id)
        try:
            lease = self._edit_guard.acquire_for_edit(scope=scope)
        except SkillsPoolEditPausedError as exc:
            raise LocalSkillEditPausedError() from exc
        try:
            skill, bot, owner_id, bot_id = self._authorize(skill_id, actor_id)
            if self._scope_for(bot, bot_id) != scope:
                raise LocalSkillNotFoundError()
            if not is_bot_ready(bot):
                raise LocalSkillNotReadyError()
            default_set = self._skill_set_repo.get_default(
                user_id=owner_id,
                bolt_id=bot_id,
                engine_type=bot.get("active_engine"),
            )
            if default_set is None:
                raise LocalSkillNotFoundError()
            changed = bool(skill["active"]) != active
            if changed:
                self._write_desired_state(
                    active=active,
                    owner_id=owner_id,
                    bot_id=bot_id,
                    skill_set_id=int(default_set["id"]),
                    skill_id=int(skill_id),
                )
                if not active:
                    try:
                        await self._cleanup_runtime_link(
                            bot=bot,
                            owner_id=owner_id,
                            bot_id=bot_id,
                            skill_name=str(skill["name"]),
                        )
                    except LocalSkillStorageError:
                        try:
                            self._write_desired_state(
                                active=True,
                                owner_id=owner_id,
                                bot_id=bot_id,
                                skill_set_id=int(default_set["id"]),
                                skill_id=int(skill_id),
                            )
                        except Exception as exc:
                            raise LocalSkillStorageError() from exc
                        if not self._sync_runtime(
                            bot=bot, owner_id=owner_id, bot_id=bot_id
                        ):
                            raise LocalSkillRuntimeSyncError()
                        raise
            if not self._sync_runtime(bot=bot, owner_id=owner_id, bot_id=bot_id):
                if changed:
                    try:
                        self._write_desired_state(
                            active=not active,
                            owner_id=owner_id,
                            bot_id=bot_id,
                            skill_set_id=int(default_set["id"]),
                            skill_id=int(skill_id),
                        )
                    except Exception as exc:
                        raise LocalSkillStorageError() from exc
                    if not self._sync_runtime(
                        bot=bot, owner_id=owner_id, bot_id=bot_id
                    ):
                        raise LocalSkillRuntimeSyncError()
                raise LocalSkillRuntimeSyncError()
            return {**skill, "active": active, "changed": changed}
        finally:
            self._edit_guard.release(lease)

    def _discover_scope(self, skill_id: str) -> BotSkillLayoutScope:
        """Find only the lock identity before serializing the authoritative read."""
        if not skill_id.isdecimal():
            raise LocalSkillNotFoundError()
        raw = self._skill_repo.get_by_id(skill_id)
        if not self._is_exact_local_skill(raw):
            raise LocalSkillNotFoundError()
        bot_id = str(raw["bolt_id"])
        owner_id = str(raw["user_id"])
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        return self._scope_for(bot, bot_id)

    def _authorize(
        self, skill_id: str, actor_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], str, str]:
        if not skill_id.isdecimal():
            raise LocalSkillNotFoundError()
        raw = self._skill_repo.get_by_id(skill_id)
        if not self._is_exact_local_skill(raw):
            raise LocalSkillNotFoundError()
        owner_id = str(raw["user_id"])
        bot_id = str(raw["bolt_id"])
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        if actor_id != owner_id:
            permission = self._collaborators.check_collaborator_permission(
                bot_id, owner_id, actor_id, PermissionLevel.MEMBER
            )
            if not permission.get("has_permission"):
                raise LocalSkillNotFoundError()
        skill = self._skill_repo.get_bot_local_skill(
            skill_id=skill_id, bot_id=bot_id, user_id=owner_id
        )
        if skill is None:
            raise LocalSkillNotFoundError()
        return skill, bot, owner_id, bot_id

    @staticmethod
    def _scope_for(bot: dict[str, Any], bot_id: str) -> BotSkillLayoutScope:
        return BotSkillLayoutScope(
            env=str(bot["env"]),
            entity_id=str(bot["entity_id"]),
            bot_id=bot_id,
        )

    @staticmethod
    def _is_exact_local_skill(skill: dict[str, Any] | None) -> bool:
        return bool(
            skill
            and skill.get("user_id")
            and skill.get("bolt_id")
            and str(skill.get("git_path") or "").startswith("local://")
        )

    def _write_desired_state(
        self,
        *,
        active: bool,
        owner_id: str,
        bot_id: str,
        skill_set_id: int,
        skill_id: int,
    ) -> None:
        if active:
            changed = self._skill_set_repo.remove_default_skill_exclusion(
                owner_id, bot_id, skill_set_id, skill_id
            )
        else:
            changed = self._skill_set_repo.add_default_skill_exclusion(
                owner_id, bot_id, skill_set_id, skill_id
            )
        if not changed:
            raise LocalSkillStorageError()

    def _sync_runtime(self, *, bot: dict[str, Any], owner_id: str, bot_id: str) -> bool:
        try:
            service = self._skill_set_service_factory.create(
                user_id=owner_id,
                entity_id=str(bot["entity_id"]),
                bot_id=bot_id,
                engine_type=bot.get("active_engine"),
                entity_type=bot.get("entity_type"),
            )
            return bool(service.sync_runtime())
        except Exception:
            return False

    async def _cleanup_runtime_link(
        self, *, bot: dict[str, Any], owner_id: str, bot_id: str, skill_name: str
    ) -> None:
        try:
            service = self._skill_set_service_factory.create(
                user_id=owner_id,
                entity_id=str(bot["entity_id"]),
                bot_id=bot_id,
                engine_type=bot.get("active_engine"),
                entity_type=bot.get("entity_type"),
            )
            cleaned = await service.skill_service.deactivate_skill(
                skill_name, bolt_id=bot_id, user_id=owner_id
            )
            if not cleaned:
                raise LocalSkillStorageError()
        except Exception as exc:
            raise LocalSkillStorageError() from exc
