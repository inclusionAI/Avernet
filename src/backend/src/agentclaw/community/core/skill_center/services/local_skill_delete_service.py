"""Recoverable deletion lifecycle for Bot-owned inactive Local Skills."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from injector import inject

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.skill_center.errors import (
    LocalSkillActiveError,
    LocalSkillEditBusyError,
    LocalSkillEditLockUnavailableError,
    LocalSkillEditPausedError,
    LocalSkillLayoutRollbackError,
    LocalSkillNotFoundError,
    LocalSkillNotReadyError,
    LocalSkillStorageError,
)
from agentclaw.community.core.skill_center.factories import SkillServiceFactory
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillSetRepository,
)
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.skill_center.errors import ActiveSkillSetReferenceError
from agentclaw.community.core.skills_pool.edit_guard import (
    SkillsPoolEditBusyError,
    SkillsPoolEditGuard,
    SkillsPoolEditLockUnavailableError,
    SkillsPoolEditPausedError,
    SkillsPoolEditRollbackError,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from agentclaw.community.core.skill_center.local_skill_delete_service_protocol import LocalSkillDeleteServiceProtocol

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )


class LocalSkillDeleteService(LocalSkillDeleteServiceProtocol):
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
        device_context_resolver_provider: Callable[[], "DeviceContextResolver"],
    ) -> None:
        self._skill_repo = skill_repo
        self._skill_set_repo = skill_set_repo
        self._bot_repo = bot_repo
        self._collaborators = collaborator_service
        self._skill_service_factory = skill_service_factory
        self._edit_guard = edit_guard
        self._device_context_resolver_provider = device_context_resolver_provider

    async def delete_local_skill(
        self, *, skill_id: str, owner_id: str, user_id: str
    ) -> None:
        scope = self._discover_scope(skill_id)
        try:
            lease = await self._edit_guard.acquire_for_edit_wait(scope=scope)
        except SkillsPoolEditBusyError as exc:
            raise LocalSkillEditBusyError() from exc
        except SkillsPoolEditRollbackError as exc:
            raise LocalSkillLayoutRollbackError() from exc
        except SkillsPoolEditLockUnavailableError as exc:
            raise LocalSkillEditLockUnavailableError() from exc
        except SkillsPoolEditPausedError as exc:
            raise LocalSkillEditPausedError() from exc
        try:
            skill, bot, resolved_owner_id, bot_id = self._authorize(
                skill_id,
                owner_id,
                user_id,
            )
            if resolved_owner_id != owner_id:
                raise LocalSkillNotFoundError()
            if self._scope_for(bot, bot_id) != scope:
                raise LocalSkillNotFoundError()
            if not is_bot_ready(bot):
                raise LocalSkillNotReadyError()
            if bool(skill["active"]):
                raise LocalSkillActiveError()
            active_custom_set_ids = {
                str(skill_set["id"])
                for skill_set in self._skill_set_repo.get_all_active_skill_sets_for_env(
                    user_id=resolved_owner_id,
                    bolt_id=bot_id,
                    engine_type=bot.get("active_engine"),
                    env=str(bot["env"]),
                )
                if not skill_set.get("is_default")
            }
            referenced_set_ids = {
                reference["skill_set_id"]
                for reference in self._skill_repo.list_skill_set_references(skill_id)
            }
            if active_custom_set_ids & referenced_set_ids:
                raise LocalSkillActiveError()
            locator = str(skill["git_path"])[len("local://") :]
            is_teclaw = self._is_teclaw(bot_id=bot_id, owner_id=resolved_owner_id)
            package = self._package_for_locator(
                bot=bot,
                owner_id=resolved_owner_id,
                bot_id=bot_id,
                is_teclaw=is_teclaw,
                locator=locator,
            )
            _, quarantine = (
                self._skill_service_factory.local_skill_package_storage(
                    entity_id=str(bot["entity_id"]),
                    owner_id=owner_id,
                    bot_id=bot_id,
                    engine_type=bot.get("active_engine"),
                    entity_type=str(bot.get("entity_type") or "staff"),
                    is_desktop=bot.get("bot_type") == "desktop",
                    is_teclaw=is_teclaw,
                    name=Path(locator).name,
                    directory_name=f".{Path(locator).name}.delete-{uuid4().hex}",
                )
            )
            try:
                await package.quarantine_to(quarantine)
            except Exception as exc:
                await self._discard(quarantine)
                raise LocalSkillStorageError() from exc
            try:
                deleted = self._skill_repo.delete_bot_local_skill(
                    skill_id=skill_id,
                    owner_id=owner_id,
                    bot_id=bot_id,
                )
                if deleted is None:
                    raise RuntimeError("Local Skill record disappeared during deletion")
            except Exception as exc:
                try:
                    restored, _ = await package.restore_from(quarantine)
                except Exception as restore_exc:
                    raise LocalSkillStorageError() from restore_exc
                if not restored:
                    raise LocalSkillStorageError() from exc
                if isinstance(exc, ActiveSkillSetReferenceError):
                    raise LocalSkillActiveError() from exc
                raise LocalSkillStorageError() from exc
            await self._discard(quarantine)
        finally:
            self._edit_guard.release(lease)

    def _package_for_locator(
        self,
        *,
        bot: dict[str, Any],
        owner_id: str,
        bot_id: str,
        is_teclaw: bool,
        locator: str,
    ):
        return self._skill_service_factory.local_skill_package_storage_for_locator(
            entity_id=str(bot["entity_id"]),
            owner_id=owner_id,
            bot_id=bot_id,
            engine_type=bot.get("active_engine"),
            entity_type=str(bot.get("entity_type") or "staff"),
            is_desktop=bot.get("bot_type") == "desktop",
            is_teclaw=is_teclaw,
            locator=locator,
        )

    async def _discard(self, storage) -> None:
        try:
            cleaned = await storage.cleanup()
        except Exception as exc:
            raise LocalSkillStorageError() from exc
        if not cleaned:
            raise LocalSkillStorageError()

    def _is_teclaw(self, *, bot_id: str, owner_id: str) -> bool:
        try:
            context = self._device_context_resolver_provider().resolve_for_bot(
                bot_id, owner_id
            )
        except Exception as exc:
            raise LocalSkillStorageError() from exc
        return context.provider == "teclaw"

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
        self, skill_id: str, owner_id: str, user_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], str, str]:
        if not skill_id.isdecimal():
            raise LocalSkillNotFoundError()
        raw = self._skill_repo.get_by_id(skill_id)
        if not self._is_exact_local_skill(raw):
            raise LocalSkillNotFoundError()
        skill_owner_id = str(raw["user_id"])
        bot_id = str(raw["bolt_id"])
        if skill_owner_id != owner_id:
            raise LocalSkillNotFoundError()
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        if user_id != owner_id:
            permission = self._collaborators.check_collaborator_permission(
                bot_id, owner_id, user_id, PermissionLevel.MEMBER
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
