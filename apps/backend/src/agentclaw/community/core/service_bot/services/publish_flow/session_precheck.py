"""Online-publish session precheck: block the upgrade while runs are in flight.

The signal is per-session ``run_status``, read through the engine-neutral
relay from the adapter's ``GET /api/aicoding/sessions``. ``run_status`` is the
session's newest run's aix ``status.kind`` passthrough (running /
awaitingApproval / awaitingHuman / awaitingLlmEvaluation / completed /
failed), and ``idle`` covers "no run / no active run / lookup failed /
enrichment timeout" — a conversation that merely has retained history never
counts. A counting error (route absent on OSS/test profiles, relay failure,
timeout) also fails open to 0 so an unavailable signal never blocks a publish.
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
#: One bounded page of sessions. Counting only *in-flight* work, not history,
#: keeps the scan small; a service bot with more conversations than this is
#: the exception, and undercounting fails open (never blocks).
SESSION_PRECHECK_LIMIT = 100

_SERVICE_BOT_TYPE = "service"


def _run_in_flight(run_status: Any) -> bool:
    """True when the session's newest run is still working.

    ``running`` and every ``awaiting*`` kind (approval / human / LLM
    evaluation, plus any future aix awaiting kinds) mean work is held in
    flight; ``completed`` / ``failed`` / ``idle`` / unknown values mean it is
    not. Unknown *non-Awaiting* future kinds fall to the fail-open side — the
    precheck must not invent blocks the engine did not report.
    """
    if not isinstance(run_status, str):
        return False
    return run_status == "running" or run_status.startswith("awaiting")


class SessionPrechecker:
    """Queries the engine relay for in-flight runs before an online upgrade.

    Only service bots are checked; personal bots always return 0.  A relay
    error or timeout is logged and returns 0 — the release proceeds
    (fail-open, same posture as the Bot overview).
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
        """Number of sessions with an in-flight run for *bot*; 0 on error."""
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
        """One page of run-status-enriched sessions; count in-flight rows."""
        result = await self._relay.call(
            bot_id=bot_id,
            owner_id=owner_id,
            method="GET",
            path="/api/aicoding/sessions",
            params={"limit": SESSION_PRECHECK_LIMIT, "offset": 0},
            timeout=self._timeout_s,
            stage=STAGE_ONLINE,
        )
        rows = result.data
        if not isinstance(rows, list):
            return 0
        return sum(1 for row in rows if _run_in_flight(row.get("run_status")))


__all__ = [
    "SESSION_PRECHECK_LIMIT",
    "SESSION_PRECHECK_TIMEOUT_S",
    "SessionPrechecker",
]
