"""Online-publish session precheck: block the upgrade when the target bot has
active sessions.

Reuses the engine-neutral ``GET /api/sessions`` route through the
engine-runtime relay (the same route ``EngineSessionActivityReader`` uses for
the Bot overview). Only the count matters here, not the session contents.

A relay error or timeout does not block the publish — the precheck returns 0
so the release proceeds (the same fail-open posture the overview takes).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from agentclaw.community.core.engine_runtime.engine_runtime_service_protocol import (
    EngineRuntimeRelayProtocol,
)
from agentclaw.community.core.engine_runtime.stage import STAGE_ONLINE
from agentclaw.community.log import get_logger

logger = get_logger()

#: Upper bound on the whole read, device resolution included — matches
#: ``SESSION_READ_TIMEOUT_S`` so the publish precheck has the same budget
#: the Bot overview uses.
SESSION_PRECHECK_TIMEOUT_S = 8.0
#: One row is enough to know whether *any* active session exists.
SESSION_PRECHECK_LIMIT = 1

_SERVICE_BOT_TYPE = "service"


class SessionPrechecker:
    """Queries the engine relay for active sessions before an online upgrade.

    Only service bots are checked; personal bots always return 0.  A relay
    error or timeout is logged and returns 0 — the release proceeds
    (fail-open, same as the Bot overview).
    """

    def __init__(
        self,
        *,
        relay: EngineRuntimeRelayProtocol,
        timeout_s: float = SESSION_PRECHECK_TIMEOUT_S,
    ) -> None:
        self._relay = relay
        self._timeout_s = timeout_s

    async def active_session_count(self, bot: Mapping[str, Any]) -> int:
        """Return the number of active sessions for *bot*, or 0 on error/timeout."""
        bot_id = str(bot.get("bot_id") or "")
        owner_id = str(bot.get("owner_id") or "")
        if not bot_id or not owner_id:
            return 0

        bot_type = str(bot.get("bot_type") or "")
        if bot_type != _SERVICE_BOT_TYPE:
            return 0

        try:
            return await asyncio.wait_for(
                self._query(bot_id=bot_id, owner_id=owner_id),
                timeout=self._timeout_s,
            )
        except Exception as exc:
            logger.warning(
                "[publish.session_precheck] session query failed bot_id=%s "
                "error_type=%s; allowing publish",
                bot_id,
                type(exc).__name__,
            )
            return 0

    async def _query(self, *, bot_id: str, owner_id: str) -> int:
        """Issue one page through the relay and return the row count."""
        result = await self._relay.call(
            bot_id=bot_id,
            owner_id=owner_id,
            method="GET",
            path="/api/sessions",
            params={"limit": SESSION_PRECHECK_LIMIT, "offset": 0},
            timeout=self._timeout_s,
            stage=STAGE_ONLINE,
        )
        data = result.data
        if isinstance(data, list):
            return len(data)
        return 0


__all__ = [
    "SESSION_PRECHECK_LIMIT",
    "SESSION_PRECHECK_TIMEOUT_S",
    "SessionPrechecker",
]
