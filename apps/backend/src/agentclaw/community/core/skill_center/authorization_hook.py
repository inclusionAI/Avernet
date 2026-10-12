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
        # Capability mutations are the edit domain: the explicit ladder, so
        # the Space-synthesized MEMBER cannot manage what it cannot edit.
        # The openapi twin rows (skill-sets writes) say the same via
        # ``Check … explicit=True``; the internal face at ``PUT
        # /api/skillsets/{set_id}`` rode this hook and admitted a space
        # member through the synthesis while its own POST/DELETE twin rows
        # (ADMIN) refused — the asymmetry the review confirmed.
        permission = self._collaborators.check_collaborator_permission(
            bot_id, owner_id, actor_id, PermissionLevel.MEMBER,
            explicit=True,
        )
        return bool(permission.get("has_permission"))


__all__ = [
    "BotCapabilityAuthorizationHookProtocol",
    "CollaboratorBotCapabilityAuthorizationHook",
]
