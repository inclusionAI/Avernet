"""Recoverable deletion lifecycle for Bot-owned inactive Local Skills."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from injector import inject

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.skill_center.errors import (
    LocalSkillActiveError,
    LocalSkillEditPausedError,
    LocalSkillNotFoundError,
    LocalSkillNotReadyError,
    LocalSkillStorageError,
)
from agentclaw.community.core.skill_center.factories import (
    LocalSkillQuarantineRepairError,
    SkillServiceFactory,
)
from agentclaw.community.core.skill_center.services.repositories import (
    SkillRepository,
    SkillSetRepository,
)
from agentclaw.community.core.skills_pool.edit_guard import (
    SkillsPoolEditGuard,
    SkillsPoolEditPausedError,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from agentclaw.community.plugin_api.local_skill_cleanup import (
    LocalSkillCleanupRepository,
)


class LocalSkillDeleteService:
    """Delete an inactive Local Skill after reversible package quarantine."""

    @inject
    def __init__(
        self,
        skill_repo: SkillRepository,
        skill_set_repo: SkillSetRepository,
        bot_repo: BotRepository,
        collaborator_service: CollaboratorServiceProtocol,
        skill_service_factory: SkillServiceFactory,
        edit_guard: SkillsPoolEditGuard,
        cleanup_repo: LocalSkillCleanupRepository,
    ) -> None:
        self._skill_repo = skill_repo
        self._skill_set_repo = skill_set_repo
        self._bot_repo = bot_repo
        self._collaborators = collaborator_service
        self._skill_service_factory = skill_service_factory
        self._edit_guard = edit_guard
        self._cleanup_repo = cleanup_repo

    async def delete_local_skill(self, *, skill_id: str, actor_id: str) -> None:
        scope = self._discover_scope(skill_id)
        try:
            lease = await self._edit_guard.acquire_for_edit_wait(scope=scope)
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
            excluded_skill_ids = self._skill_set_repo.get_excluded_skills(
                owner_id, bot_id, int(default_set["id"])
            )
            if int(skill_id) not in {
                int(excluded_id) for excluded_id in excluded_skill_ids
            }:
                raise LocalSkillActiveError()
            active_custom_set_ids = {
                str(skill_set["id"])
                for skill_set in self._skill_set_repo.get_all_active_skill_sets_for_env(
                    user_id=owner_id,
                    bolt_id=bot_id,
                    engine_type=bot.get("active_engine"),
                    env=str(bot["env"]),
                )
            }
            referenced_set_ids = {
                reference["skill_set_id"]
                for reference in self._skill_repo.list_skill_set_references(skill_id)
            }
            if active_custom_set_ids & referenced_set_ids:
                raise LocalSkillActiveError()
            locator = str(skill["git_path"])[len("local://") :]
            package = (
                self._skill_service_factory.local_skill_package_storage_for_locator(
                    owner_id=owner_id,
                    bot_id=bot_id,
                    engine_type=bot.get("active_engine"),
                    locator=locator,
                )
            )
            quarantine_locator, quarantine = (
                self._skill_service_factory.local_skill_package_storage(
                    owner_id=owner_id,
                    bot_id=bot_id,
                    engine_type=bot.get("active_engine"),
                    name=Path(locator).name,
                    directory_name=f".{Path(locator).name}.delete-{uuid4().hex}",
                )
            )
            try:
                await package.quarantine_to(quarantine)
            except LocalSkillQuarantineRepairError as exc:
                # Quarantine is the sole complete copy until an operator can
                # repair the partially deleted authoritative package. Never
                # purge it in this fail-closed state.
                self._record_repair_required(
                    bot=bot,
                    owner_id=owner_id,
                    bot_id=bot_id,
                    skill_id=skill_id,
                    quarantine_locator=quarantine_locator,
                )
                raise LocalSkillStorageError() from exc
            except Exception as exc:
                await self._discard_or_record_precommit_quarantine(
                    bot=bot,
                    owner_id=owner_id,
                    bot_id=bot_id,
                    skill_id=skill_id,
                    quarantine=quarantine,
                    quarantine_locator=quarantine_locator,
                )
                raise LocalSkillStorageError() from exc
            try:
                work_id = self._skill_repo.delete_bot_local_skill(
                    skill_id=skill_id,
                    owner_id=owner_id,
                    bot_id=bot_id,
                    quarantine_locator=quarantine_locator,
                )
                if work_id is None:
                    raise RuntimeError("Local Skill record disappeared during deletion")
            except Exception as exc:
                try:
                    restored, quarantine_purged = await package.restore_from(quarantine)
                except Exception as restore_exc:
                    self._record_repair_required(
                        bot=bot,
                        owner_id=owner_id,
                        bot_id=bot_id,
                        skill_id=skill_id,
                        quarantine_locator=quarantine_locator,
                    )
                    raise LocalSkillStorageError() from restore_exc
                if not restored:
                    self._record_repair_required(
                        bot=bot,
                        owner_id=owner_id,
                        bot_id=bot_id,
                        skill_id=skill_id,
                        quarantine_locator=quarantine_locator,
                    )
                    raise LocalSkillStorageError() from exc
                if not quarantine_purged:
                    try:
                        work_id = self._cleanup_repo.record_pending(
                            env=str(bot["env"]),
                            owner_id=owner_id,
                            bot_id=bot_id,
                            skill_id=skill_id,
                            package_locator=quarantine_locator,
                            requires_runtime_restore=False,
                        )
                    except Exception as record_exc:
                        raise LocalSkillStorageError() from record_exc
                    if work_id is None:
                        raise LocalSkillStorageError()
                raise LocalSkillStorageError() from exc
            try:
                purged = await quarantine.cleanup()
            except Exception:
                purged = False
            if not purged:
                return
            try:
                marked = self._cleanup_repo.mark_cleaned(
                    work_id=work_id,
                    env=str(bot["env"]),
                    owner_id=owner_id,
                    bot_id=bot_id,
                )
            except Exception as exc:
                raise LocalSkillStorageError() from exc
            if not marked:
                raise LocalSkillStorageError()
        finally:
            self._edit_guard.release(lease)

    def _record_repair_required(
        self,
        *,
        bot: dict[str, Any],
        owner_id: str,
        bot_id: str,
        skill_id: str,
        quarantine_locator: str,
    ) -> None:
        try:
            work_id = self._cleanup_repo.record_repair_required(
                env=str(bot["env"]),
                owner_id=owner_id,
                bot_id=bot_id,
                skill_id=skill_id,
                package_locator=quarantine_locator,
            )
        except Exception as exc:
            raise LocalSkillStorageError() from exc
        if work_id is None:
            raise LocalSkillStorageError()

    async def _discard_or_record_precommit_quarantine(
        self,
        *,
        bot: dict[str, Any],
        owner_id: str,
        bot_id: str,
        skill_id: str,
        quarantine,
        quarantine_locator: str,
    ) -> None:
        """Remove a failed pre-commit copy, or leave it durably retryable."""
        try:
            if await quarantine.cleanup():
                return
        except Exception:
            pass
        try:
            work_id = self._cleanup_repo.record_pending(
                env=str(bot["env"]),
                owner_id=owner_id,
                bot_id=bot_id,
                skill_id=skill_id,
                package_locator=quarantine_locator,
                requires_runtime_restore=False,
            )
        except Exception as exc:
            raise LocalSkillStorageError() from exc
        if work_id is None:
            raise LocalSkillStorageError()

    def _discover_scope(self, skill_id: str) -> BotSkillLayoutScope:
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
            env=str(bot["env"]), entity_id=str(bot["entity_id"]), bot_id=bot_id
        )

    @staticmethod
    def _is_exact_local_skill(skill: dict[str, Any] | None) -> bool:
        return bool(
            skill
            and skill.get("user_id")
            and skill.get("bolt_id")
            and str(skill.get("git_path") or "").startswith("local://")
        )
