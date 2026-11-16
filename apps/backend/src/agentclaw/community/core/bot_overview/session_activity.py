"""Last-session time for the Bot overview, read from the Bot's engine runtime.

The platform reaches every engine through one engine-neutral route, the
adapter's ``GET /api/sessions`` (``engine/adapter/.../api/session/router.py``),
forwarded by the engine-runtime relay. So the overview needs no per-engine code
for it: OpenClaw and Claude Code both answer that route, each with
timezone-aware ISO ``gmt_modified`` values.

Only the newest ``gmt_modified`` leaves this module. Titles, messages, user
ids and session keys in the engine's answer never do, because the overview
carries non-user data only.

A Bot whose runtime cannot be reached (no device bound, a stage that is not
live, an engine error or timeout) reports no time rather than failing the
overview. The overview has to answer even when the sandbox is down.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.engine_runtime.engine_runtime_service_protocol import (
    EngineRuntimeRelayProtocol,
)
from agentclaw.community.core.engine_runtime.stage import STAGE_DRAFT, STAGE_ONLINE
from agentclaw.community.log import get_logger

logger = get_logger()

#: Rows per page. Kept small on purpose: OpenClaw fetches ``chat.history``
#: for every row of the page it returns, so the page size is that many extra
#: RPCs.
SESSION_SCAN_LIMIT = 5
#: Pages to scan before giving up. Claude Code pages *before* it drops its
#: BCS group sessions, so a page can come back empty while older ordinary
#: sessions exist. The engine lists newest first, so the first page that
#: yields any time holds the newest visible session and the scan stops
#: there. A Bot with no sessions at all costs every page; this bounds it.
SESSION_SCAN_MAX_PAGES = 4
#: Upper bound on the whole read, device resolution included. The relay's
#: device leg can block for up to 30 s on a slow provider. A request that
#: needs no user credential must not inherit that wait.
SESSION_READ_TIMEOUT_S = 8.0

_SERVICE_BOT_TYPE = "service"


@runtime_checkable
class BotSessionActivityReaderProtocol(Protocol):
    """When a Bot last had session activity."""

    async def last_session_at(self, bot: Mapping[str, Any]) -> datetime | None:
        """UTC time of the newest session, or ``None`` when none is known."""
        ...


def _stage_for(bot: Mapping[str, Any]) -> str:
    """The runtime whose sessions count: a published service Bot's live one."""
    if str(bot.get("bot_type") or "") == _SERVICE_BOT_TYPE:
        return STAGE_ONLINE
    return STAGE_DRAFT


def _parse(value: Any) -> datetime | None:
    """An aware UTC datetime from an engine ``gmt_modified``, or ``None``."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Both engines emit aware values. A naive one has no reliable zone, so
        # report nothing rather than guess one.
        return None
    return parsed.astimezone(timezone.utc)


def newest_session_time(sessions: Any) -> datetime | None:
    """The newest ``gmt_modified`` across the engine's session rows."""
    if not isinstance(sessions, list):
        return None
    times = [
        parsed
        for row in sessions
        if isinstance(row, Mapping)
        and (parsed := _parse(row.get("gmt_modified"))) is not None
    ]
    return max(times, default=None)


class EngineSessionActivityReader(BotSessionActivityReaderProtocol):
    """Reads the newest session time through the engine-runtime relay."""

    def __init__(
        self,
        *,
        relay: EngineRuntimeRelayProtocol,
        timeout_s: float = SESSION_READ_TIMEOUT_S,
    ) -> None:
        self._relay = relay
        self._timeout_s = timeout_s

    async def last_session_at(self, bot: Mapping[str, Any]) -> datetime | None:
        bot_id = str(bot.get("bot_id") or "")
        owner_id = str(bot.get("owner_id") or "")
        if not bot_id or not owner_id:
            return None
        try:
            # One budget for the whole scan, device resolution included.
            return await asyncio.wait_for(
                self._scan(bot_id=bot_id, owner_id=owner_id, stage=_stage_for(bot)),
                timeout=self._timeout_s,
            )
        except Exception as exc:
            logger.warning(
                "[bot_overview.sessions] last-session read failed bot_id=%s "
                "error_type=%s; reporting no session time",
                bot_id,
                type(exc).__name__,
            )
            return None

    async def _scan(self, *, bot_id: str, owner_id: str, stage: str) -> datetime | None:
        for page in range(SESSION_SCAN_MAX_PAGES):
            # The Bot's own Owner is the caller: this is a platform read on the
            # Bot's behalf, and the relay's operator gate admits the Owner.
            result = await self._relay.call(
                bot_id=bot_id,
                owner_id=owner_id,
                method="GET",
                path="/api/sessions",
                params={
                    "limit": SESSION_SCAN_LIMIT,
                    "offset": page * SESSION_SCAN_LIMIT,
                },
                timeout=self._timeout_s,
                stage=stage,
            )
            newest = newest_session_time(result.data)
            if newest is not None:
                return newest
        return None


__all__ = [
    "BotSessionActivityReaderProtocol",
    "EngineSessionActivityReader",
    "SESSION_READ_TIMEOUT_S",
    "SESSION_SCAN_MAX_PAGES",
    "SESSION_SCAN_LIMIT",
    "newest_session_time",
]
