"""Registered authorization hook for Bot capability mutations."""

from __future__ import annotations

from typing import Protocol

from injector import inject

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)


class BotCapabilityAuthorizationHookProtocol(Protocol):
    """Central authorization extension point used by capability services."""

    def can_manage_bot(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> bool: ...


class CollaboratorBotCapabilityAuthorizationHook:
    """Production/local slot backed by the existing Bot collaborator policy."""

    @inject
    def __init__(self, collaborators: CollaboratorServiceProtocol) -> None:
        self._collaborators = collaborators

    def can_manage_bot(self, *, bot_id: str, owner_id: str, actor_id: str) -> bool:
        if actor_id == owner_id:
            return True
        permission = self._collaborators.check_collaborator_permission(
            bot_id, owner_id, actor_id, PermissionLevel.MEMBER
        )
        return bool(permission.get("has_permission"))


__all__ = [
    "BotCapabilityAuthorizationHookProtocol",
    "CollaboratorBotCapabilityAuthorizationHook",
]
