"""Runtime-owned package deletion for Bot-owned inactive Local Skills."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from injector import inject

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_management.addressing import BotAddressResolver, stable_resource_owner_id
from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.skill_center.errors import (
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
from agentclaw.community.core.skills_pool.edit_guard import (
    SkillsPoolEditBusyError,
    SkillsPoolEditGuard,
    SkillsPoolEditLockUnavailableError,
    SkillsPoolEditPausedError,
    SkillsPoolEditRollbackError,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from agentclaw.community.core.skill_center.local_skill_delete_service_protocol import LocalSkillDeleteServiceProtocol
from agentclaw.community.core.skill_center.local_skill_security_upload_hook import (
    LocalSkillSecurityUploadHook,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )


class LocalSkillDeleteService(LocalSkillDeleteServiceProtocol):
    """Delete an inactive Local Skill through one package-level runtime call."""

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
        security_hook_provider: Callable[[], LocalSkillSecurityUploadHook],
        security_observation_enabled: bool,
    ) -> None:
        self._skill_repo = skill_repo
        self._skill_set_repo = skill_set_repo
        self._bot_repo = bot_repo
        self._bot_addresses = BotAddressResolver(bot_repo)
        self._collaborators = collaborator_service
        self._skill_service_factory = skill_service_factory
        self._edit_guard = edit_guard
        self._device_context_resolver_provider = device_context_resolver_provider
        self._security_hook_provider = security_hook_provider
        self._security_observation_enabled = security_observation_enabled

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
            owner_id = resolved_owner_id
            if self._scope_for(bot, bot_id) != scope:
                raise LocalSkillNotFoundError()
            if not is_bot_ready(bot):
                raise LocalSkillNotReadyError()
            self._skill_repo.require_unreferenced_for_delete(skill_id)
            locator = str(skill["git_path"])[len("local://") :]
            is_teclaw = self._is_teclaw(bot_id=bot_id, owner_id=resolved_owner_id)
            package = self._package_for_locator(
                bot=bot,
                owner_id=resolved_owner_id,
                bot_id=bot_id,
                is_teclaw=is_teclaw,
                locator=locator,
                skill_name=str(skill["name"]),
            )
            try:
                package_exists = await package.exists()
                package_deleted = await package.delete() if package_exists else True
            except Exception as exc:
                raise LocalSkillStorageError() from exc
            if not package_deleted:
                raise LocalSkillStorageError()
            try:
                deleted = self._skill_repo.delete_bot_local_skill(
                    skill_id=skill_id,
                    owner_id=owner_id,
                    bot_id=bot_id,
                )
                if not deleted:
                    raise RuntimeError("Local Skill record disappeared during deletion")
            except Exception as exc:
                raise LocalSkillStorageError() from exc
            if self._security_observation_enabled:
                try:
                    self._security_hook_provider().record_deletion(
                        bot_id=bot_id, owner_id=owner_id,
                        actor_id=user_id, skill_id=int(skill_id),
                    )
                except Exception:
                    logger.exception(
                        "Local Skill deletion security sync could not be recorded "
                        "bot_id=%s owner_id=%s skill_id=%s actor_id=%s",
                        bot_id, owner_id, skill_id, user_id,
                    )
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
        skill_name: str,
    ):
        try:
            return self._skill_service_factory.local_skill_package_storage_for_locator(
                entity_id=str(bot["entity_id"]),
                owner_id=owner_id,
                bot_id=bot_id,
                engine_type=bot.get("active_engine"),
                entity_type=str(bot.get("entity_type") or "staff"),
                is_desktop=bot.get("bot_type") == "desktop",
                is_teclaw=is_teclaw,
                locator=locator,
                skill_name=skill_name,
            )
        except Exception as exc:
            raise LocalSkillStorageError() from exc

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
        try:
            bot = self._bot_addresses.resolve(
                bot_id=bot_id, legacy_owner_id=owner_id
            ).bot
        except BotAddressNotFoundError:
            raise LocalSkillNotFoundError() from None
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
        try:
            bot = self._bot_addresses.resolve(
                bot_id=bot_id, legacy_owner_id=owner_id
            ).bot
        except BotAddressNotFoundError:
            raise LocalSkillNotFoundError() from None
        owner_id = stable_resource_owner_id(bot)
        if skill_owner_id != owner_id:
            raise LocalSkillNotFoundError()
        current_owner = str(bot["owner_id"])
        if user_id != current_owner:
            permission = self._collaborators.check_collaborator_permission(
                bot_id, current_owner, user_id, PermissionLevel.MEMBER
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
