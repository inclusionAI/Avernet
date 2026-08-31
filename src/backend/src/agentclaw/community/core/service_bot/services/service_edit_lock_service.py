"""Narrow service-Bot collaborative edit-lock application service."""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.collaborator_lock_service_protocol import (
    CollaboratorLockServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.collaborator_service_protocol import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.protocols import (
    resolve_operable_permission_level,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.service_bot.errors import (
    ServicePublicationNotFoundError,
    ServicePublicationUnsupportedError,
)
from agentclaw.community.core.service_bot.service_edit_lock_service_protocol import (
    ServiceEditLockInfo,
    ServiceEditLockServiceProtocol,
)
from agentclaw.community.utils.env_utils import get_current_env


class ServiceEditLockService(ServiceEditLockServiceProtocol):
    """Resolve and authorize edit locks without constructing publication flow."""

    def __init__(
        self,
        *,
        bot_repo: BotRepository,
        collaborator_service: CollaboratorServiceProtocol,
        lock_service: CollaboratorLockServiceProtocol,
    ) -> None:
        self._bot_repo = bot_repo
        self._collaborator_service = collaborator_service
        self._lock_service = lock_service

    def _resolve_bot(
        self,
        bot_id: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise ServicePublicationNotFoundError("bot not found")

        level = resolve_operable_permission_level(
            self._collaborator_service,
            bot=bot,
            user_id=actor_id,
            owner_id=owner_id,
            env=get_current_env(),
        )
        if level < PermissionLevel.MEMBER:
            # COSEC: mask authorization failures to prevent Bot-ID probing.
            raise ServicePublicationNotFoundError("bot not found")
        if bot.get("bot_type") != "service":
            raise ServicePublicationUnsupportedError("bot is not a service bot")
        return bot

    def _lock_info(
        self,
        bot: dict[str, Any],
        *,
        actor_id: str,
    ) -> ServiceEditLockInfo:
        info = self._lock_service.get_lock_info(
            bot["bot_id"], bot["owner_id"], actor_id
        )
        return ServiceEditLockInfo(
            lock=info.lock,
            holder_name=info.holder_name,
            has_collaborators=info.has_collaborators,
            is_owner=info.is_owner,
            need_lock=info.has_collaborators,
        )

    def get_lock(
        self,
        bot_id: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> ServiceEditLockInfo:
        bot = self._resolve_bot(bot_id, actor_id=actor_id, owner_id=owner_id)
        return self._lock_info(bot, actor_id=actor_id)

    def _has_collaborators(
        self,
        bot: dict[str, Any],
        *,
        actor_id: str,
    ) -> bool:
        return self._lock_info(bot, actor_id=actor_id).has_collaborators

    def acquire_lock(
        self,
        bot_id: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> Any:
        bot = self._resolve_bot(bot_id, actor_id=actor_id, owner_id=owner_id)
        if not self._has_collaborators(bot, actor_id=actor_id):
            return None
        return self._lock_service.acquire_lock(bot_id, bot["owner_id"], actor_id)

    def release_lock(
        self,
        bot_id: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> bool:
        bot = self._resolve_bot(bot_id, actor_id=actor_id, owner_id=owner_id)
        return bool(
            self._lock_service.release_lock(
                bot_id,
                bot["owner_id"],
                actor_id,
                False,
            )
        )

    def steal_lock(
        self,
        bot_id: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> Any:
        bot = self._resolve_bot(bot_id, actor_id=actor_id, owner_id=owner_id)
        if not self._has_collaborators(bot, actor_id=actor_id):
            return None
        return self._lock_service.steal_lock(bot_id, bot["owner_id"], actor_id)
