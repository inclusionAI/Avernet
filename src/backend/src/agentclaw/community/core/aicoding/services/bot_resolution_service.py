"""AICoding-specific bot resolution helpers.

This module keeps collaborator-aware bot lookup out of the generic bot service.
It is only used by the AICoding DIMA workspace endpoint, where the caller may
be either the bot owner or a collaborator.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from injector import inject

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.bot.collaborator import (
    CollaboratorRepositoryProtocol,
)
from agentclaw.community.utils.env_utils import get_current_env

log = logging.getLogger("aicoding-bot-resolution")


class AicodingBotResolutionService:
    """Resolve the real bot owner for AICoding workspace operations."""

    @inject
    def __init__(
        self,
        bot_repo: BotRepository,
        collaborator_repo: CollaboratorRepositoryProtocol,
    ) -> None:
        self._bot_repo = bot_repo
        self._collaborator_repo = collaborator_repo

    def resolve_bot_for_dima_workspace(
        self,
        bot_id: str,
        requested_owner_id: str,
        operator_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Return the bot record visible to the caller for DIMA workspace setup.

        Resolution order:
        1. Exact lookup by the requested owner id.
        2. Exact lookup by the current operator id.
        3. Collaborator records for the current operator, which recover the real
           owner id when the caller is a collaborator and the request carried a
           collaborator-scoped user_id.
        """
        for owner_id in self._candidate_owner_ids(requested_owner_id, operator_id):
            bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
            if bot:
                return bot

        real_owner_id = self._resolve_owner_from_collaborators(bot_id, operator_id)
        if not real_owner_id:
            return None

        return self._bot_repo.get_by_id_and_owner(bot_id, real_owner_id)

    @staticmethod
    def _candidate_owner_ids(*owner_ids: str) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for owner_id in owner_ids:
            if not owner_id or owner_id in seen:
                continue
            seen.add(owner_id)
            result.append(owner_id)
        return result

    def _resolve_owner_from_collaborators(self, bot_id: str, operator_id: str) -> Optional[str]:
        try:
            collaborators = self._collaborator_repo.list_by_user(operator_id, get_current_env())
        except Exception as exc:
            log.warning(
                "[resolve_bot_for_dima_workspace] failed to list collaborators: bot_id=%s operator_id=%s error=%s",
                bot_id,
                operator_id,
                exc,
            )
            return None

        for record in collaborators:
            if str(getattr(record, "bot_id", "") or "") != bot_id:
                continue
            owner_id = str(getattr(record, "owner_id", "") or "")
            if owner_id:
                return owner_id
        return None
