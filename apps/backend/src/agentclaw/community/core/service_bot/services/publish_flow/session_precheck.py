"""Online-publish session precheck: block the upgrade while a session on the
online runtime has work in flight.

Wiring-only skeleton: the engine adapter currently exposes **no** running-work
signal — ``GET /api/sessions`` lists retained conversations without an
execution-state filter (counting its rows would block every bot with history,
forever), and the run-status-enriched ``GET /api/aicoding/sessions`` is mounted
only by corp internal router profiles, not by the engines this flow ships to
(verified absent on the simulator stack). Until the engine-side Active Session
query API lands (sibling work item 「实现 OpenClaw Engine Active Session 查询
API」, Dima 2026090100118725722), there is no signal this precheck can honestly
consume.

So this class parks the consumption point: the DI entry, the precheck call site
in :mod:`release_phase_mixin`, and the block-on-positive-count behavior are all
in place and under test (via the prechecker seam), but ``active_session_count``
reports 0 — fail-open — rather than inventing a count from a route that does not
exist. Wiring the real query in is then a change to one method.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentclaw.community.core.engine_runtime.engine_runtime_service_protocol import (
    EngineRuntimeRelayProtocol,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: Budget the real query must respect once it lands — matches
#: ``SESSION_READ_TIMEOUT_S`` so the publish precheck has the same budget
#: the Bot overview uses.
SESSION_PRECHECK_TIMEOUT_S = 8.0

_SERVICE_BOT_TYPE = "service"


class SessionPrechecker:
    """Counts sessions with work in flight on a bot's online runtime.

    The relay is carried (the DI entry and call site are wired) but not yet
    called: no engine route reports running work today, so the count is
    intentionally 0 — an unavailable signal must never block a publish.
    """

    def __init__(
        self,
        *,
        relay: EngineRuntimeRelayProtocol,
        timeout_s: float = SESSION_PRECHECK_TIMEOUT_S,
    ) -> None:
        self._relay = relay
        self._timeout_s = timeout_s

    @property
    def relay(self) -> EngineRuntimeRelayProtocol:
        """The relay the real query will issue through once it exists."""
        return self._relay

    async def active_session_count(self, bot: Mapping[str, Any]) -> int:
        """Number of sessions with work in flight for *bot*.

        Always 0 until the engine-side Active Session query API exists
        (Dima 2026090100118725722): without a real running-work signal the
        precheck fails open so retained history alone can never block a
        publish. Personal bots would never be checked either way.
        """
        # TODO(2026090100118725722): once the engine exposes an active-session
        # (running-work) query, call it here through self._relay with
        # stage=STAGE_ONLINE inside asyncio.wait_for(..., self._timeout_s),
        # mirroring the fail-open error handling of EngineSessionActivityReader,
        # and return the in-flight count. Do NOT count plain session listings —
        # they include finished conversations.
        _ = _SERVICE_BOT_TYPE  # kept for the real query's service-bot gate
        bot_id = str(bot.get("bot_id") or "")
        if bot_id:
            logger.debug(
                "[publish.session_precheck] no running-work signal available "
                "yet; allowing publish bot_id=%s",
                bot_id,
            )
        return 0


__all__ = [
    "SESSION_PRECHECK_TIMEOUT_S",
    "SessionPrechecker",
]
