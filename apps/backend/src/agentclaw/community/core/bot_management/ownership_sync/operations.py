"""Administrator-only resubmission; reads current state and never overrides it."""

from __future__ import annotations

import logging

from agentclaw.community.core.bot_management.bcn_identity import bcn_bot_reference
from agentclaw.community.core.bot_management.ownership_sync.repair import (
    OwnershipSyncRepairService,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.kernel.bot_manager_sync import SubmissionResult, SyncKey
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

logger = logging.getLogger(__name__)


class OwnershipRepairError(ValueError):
    """Stable operations rejection, independent of HTTP."""


class OwnershipSyncOperations:
    def __init__(
        self,
        bots: BotRepository,
        repair: OwnershipSyncRepairService,
        administrators: frozenset[str],
        env: str,
    ) -> None:
        self._bots = bots
        self._repair = repair
        self._administrators = administrators
        self._env = env

    def request_repair(self, *, bot_pk: int, actor_id: str) -> dict[str, object]:
        if actor_id not in self._administrators:
            raise OwnershipRepairError("ADMIN_REQUIRED")
        if not self._env:
            raise OwnershipRepairError("OWNERSHIP_SYNC_UNAVAILABLE")
        bot = self._bots.get_by_pk(bot_pk)
        if bot is None or bot.get("env") != self._env or bot.get("is_delete", 0):
            raise OwnershipRepairError("BOT_NOT_FOUND")
        if bot.get("bot_type") != "service" or bot["bot_id"] == "default":
            raise OwnershipRepairError("SERVICE_BOT_REQUIRED")
        try:
            address = bcn_bot_reference(bot)
        except ValueError as exc:
            raise OwnershipRepairError("INVALID_BCN_ADDRESS") from exc
        key = SyncKey(get_current_avernet_tenant(), self._env, bot_pk)
        result = self._repair.request_bot(key)
        logger.info(
            "ownership_repair actor_id=%s tenant=%s env=%s bot_pk=%s admission=%s",
            actor_id,
            key.avernet_tenant,
            key.env,
            bot_pk,
            result.value,
        )
        if result not in {SubmissionResult.QUEUED, SubmissionResult.COALESCED}:
            raise OwnershipRepairError("OWNERSHIP_SYNC_UNAVAILABLE")
        return {
            "bot_pk": bot_pk,
            "bcn_bot_id": address,
            "submission": result.value,
            "delivery_confirmed": False,
        }
