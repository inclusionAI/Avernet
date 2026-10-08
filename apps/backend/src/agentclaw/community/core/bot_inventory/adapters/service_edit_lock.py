"""Batch service-Bot edit-lock projection for the unified inventory."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any, Mapping

from agentclaw.community.core.bot_inventory.protocols import ServiceEditLockPort
from agentclaw.community.core.bot_inventory.types import ServiceEditLockState
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLockRepositoryProtocol,
    CollaboratorRepositoryProtocol,
)
from agentclaw.community.utils.env_utils import get_current_env


class ServiceEditLockView(ServiceEditLockPort):
    """Read collaborator and lock rows in two queries for a page of Bots."""

    def __init__(
        self,
        collaborator_repo: CollaboratorRepositoryProtocol,
        lock_repo: BotCollabLockRepositoryProtocol,
    ) -> None:
        self._collaborator_repo = collaborator_repo
        self._lock_repo = lock_repo

    @staticmethod
    def _lock_key(bot_id: str, owner_id: str) -> str:
        return f"{bot_id}:{owner_id}"

    def states_for_bots(
        self, *, bots: Sequence[Mapping[str, Any]]
    ) -> Mapping[tuple[str, str], ServiceEditLockState]:
        bots_by_pair: dict[tuple[str, str], Mapping[str, Any]] = {}
        for bot in bots:
            bot_id = str(bot.get("bot_id") or "")
            owner_id = str(bot.get("owner_id") or "")
            if bot_id and owner_id:
                bots_by_pair[(bot_id, owner_id)] = bot
        if not bots_by_pair:
            return {}

        pairs = list(bots_by_pair)
        collaborators_by_pair: dict[tuple[str, str], list[Any]] = defaultdict(list)
        for collaborator in self._collaborator_repo.list_by_bot_owner_pairs(
            pairs, get_current_env()
        ):
            collaborators_by_pair[
                (collaborator.bot_id, collaborator.owner_id)
            ].append(collaborator)

        locks_by_key = {
            lock.lock_key: lock
            for lock in self._lock_repo.list_by_keys(
                [self._lock_key(bot_id, owner_id) for bot_id, owner_id in pairs]
            )
        }

        states: dict[tuple[str, str], ServiceEditLockState] = {}
        for pair, bot in bots_by_pair.items():
            bot_id, owner_id = pair
            collaborators = collaborators_by_pair[pair]
            has_collaborators = bool(collaborators)
            lock = (
                locks_by_key.get(self._lock_key(bot_id, owner_id))
                if has_collaborators
                else None
            )
            holder_user_id = lock.holder_user_id if lock else None
            holder_name = None
            if holder_user_id == owner_id:
                holder_name = str(bot.get("owner_name") or "") or None
            elif holder_user_id:
                holder_name = next(
                    (
                        collaborator.user_name
                        for collaborator in collaborators
                        if collaborator.user_id == holder_user_id
                    ),
                    None,
                )
            states[pair] = ServiceEditLockState(
                locked=lock is not None,
                holder_user_id=holder_user_id,
                holder_name=holder_name,
                has_collaborators=has_collaborators,
                is_owner_holder=holder_user_id == owner_id,
            )
        return states
