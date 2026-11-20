"""Best-effort secbaas grant reconciliation for an owner's live Bots."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from agentclaw.community.core.bot_management.bcn_identity import bcn_bot_reference
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.task.bot_grant_reconciliation_service_protocol import (
    BotGrantReconciliationServiceProtocol,
)
from agentclaw.community.core.task.task_grant_service_protocol import (
    TaskClaimGrantServiceProtocol,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class BotGrantReconciliationService(BotGrantReconciliationServiceProtocol):
    """Sequentially grant owned Bots without affecting the initiating request.

    Repository reads are moved off the event loop. Each secbaas call has its own
    timeout and failure boundary, so one malformed or unavailable Bot never
    prevents later Bots from converging on a future login.
    """

    def __init__(
        self,
        *,
        bots: BotRepository,
        grants: TaskClaimGrantServiceProtocol,
        page_size: int = 100,
        grant_timeout_seconds: float = 10.0,
    ) -> None:
        self._bots = bots
        self._grants = grants
        self._page_size = page_size
        self._grant_timeout_seconds = grant_timeout_seconds

    async def reconcile(
        self,
        *,
        owner_id: str,
        cookie: str,
        referer: str,
        bot_id: str | None = None,
    ) -> None:
        try:
            bots = await self._load_bots(owner_id=owner_id, bot_id=bot_id)
        except Exception as exc:  # noqa: BLE001 best-effort background compensation
            logger.warning(
                "[task][bot-grant-reconcile] bot lookup failed owner_id=%s bot_id=%s error_type=%s",
                owner_id,
                bot_id or "*",
                type(exc).__name__,
            )
            return

        if bot_id is not None and not bots:
            logger.warning(
                "[task][bot-grant-reconcile] owned bot not found owner_id=%s bot_id=%s",
                owner_id,
                bot_id,
            )
            return

        for bot in bots:
            await self._grant_one(
                bot=bot,
                owner_id=owner_id,
                cookie=cookie,
                referer=referer,
            )

    async def _load_bots(
        self, *, owner_id: str, bot_id: str | None
    ) -> list[Mapping[str, Any]]:
        if bot_id is not None:
            bot = await asyncio.to_thread(
                self._bots.get_by_id_and_owner,
                bot_id,
                owner_id,
            )
            return [bot] if bot is not None else []

        page = 1
        loaded: list[Mapping[str, Any]] = []
        while True:
            total, rows = await asyncio.to_thread(
                self._bots.list_by_owner,
                owner_id,
                page,
                self._page_size,
            )
            loaded.extend(rows)
            if len(loaded) >= total or len(rows) < self._page_size:
                return loaded
            page += 1

    async def _grant_one(
        self,
        *,
        bot: Mapping[str, Any],
        owner_id: str,
        cookie: str,
        referer: str,
    ) -> None:
        product_bot_id = str(bot.get("bot_id") or "")
        try:
            bcs_bot_id = bcn_bot_reference(bot)
            await asyncio.wait_for(
                self._grants.grant(
                    bcs_bot_id=bcs_bot_id,
                    cookie=cookie,
                    referer=referer,
                    operator=owner_id,
                ),
                timeout=self._grant_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 isolate one Bot and continue
            logger.warning(
                "[task][bot-grant-reconcile] grant failed owner_id=%s bot_id=%s error_type=%s",
                owner_id,
                product_bot_id,
                type(exc).__name__,
            )


__all__ = ["BotGrantReconciliationService"]
