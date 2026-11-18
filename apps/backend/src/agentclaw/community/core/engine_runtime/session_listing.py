"""Bounded scan of one authorized runtime; no cross-request data/permission cache."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
from typing import Any

from injector import inject

from agentclaw.community.core.engine_runtime.engine_runtime_service_protocol import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.errors import (
    EngineSessionCursorError, EngineSessionScanDepthError, EngineUpstreamError,
)
from agentclaw.community.core.engine_runtime.models import BotFacts
from agentclaw.community.core.engine_runtime.session_listing_protocol import (
    SessionSource, SessionSourceListingProtocol, SessionSourceWindow,
)
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTimeoutError

MAX_SCAN_DEPTH = 2000
MAX_READS = 2
READ_BUDGET_SECONDS = 3.0
# Verified implementations: OpenClaw _session.py/shared/utils.py and the
# Claude Code sessions.list gateway. Their filter takes an actor, not an owner.
# Other engines must not silently ignore a restriction we delegated to them.
_NATIVE_MINE_ENGINES = frozenset({"openclaw", "aicoding", "claude_code"})
_CREATOR_PREFIX = re.compile(r"^user:([^:]+):")
_CREATOR_SUFFIX = re.compile(r"(?:^|:)(?:user|direct):([^:]+)$")


def _user(value: str) -> str:
    normalized = value.strip().casefold()
    return normalized.lstrip("0") or "0" if normalized.isascii() and normalized.isdigit() else normalized


def _matches(item: dict[str, Any], caller: str, source: SessionSource) -> bool:
    key = item.get("id")
    if not isinstance(key, str) or not key:
        raise EngineUpstreamError("session candidate has no id")
    # Task dispatch identities are not human session creators.
    if key.endswith(":agent:member-task"):
        return source == "mine"
    match = _CREATOR_PREFIX.search(key) or _CREATOR_SUFFIX.search(key)
    if match is None:
        return source == "mine"
    mine = _user(match.group(1)) == _user(caller)
    return mine if source == "mine" else not mine


def _encode(offset: int, returned: int, scope: str) -> str:
    raw = json.dumps([1, offset, returned, scope], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(cursor: str | None, scope: str) -> tuple[int, int]:
    if cursor is None:
        return 0, 0
    try:
        if len(cursor) > 512:
            raise ValueError
        data = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
        if (not isinstance(data, list) or len(data) != 4 or data[0] != 1
                or type(data[1]) is not int or type(data[2]) is not int
                or not 0 <= data[2] <= data[1] <= MAX_SCAN_DEPTH or data[3] != scope):
            raise ValueError
        return data[1], data[2]
    except (ValueError, TypeError, UnicodeError):
        raise EngineSessionCursorError("invalid session source cursor") from None


class SessionSourceListingService(SessionSourceListingProtocol):
    @inject
    def __init__(self, relay: EngineRuntimeRelayProtocol) -> None:
        self._relay = relay

    async def list_sessions(
        self, *, facts: BotFacts, caller_id: str, stage: str,
        source: SessionSource, page_size: int, cursor: str | None = None,
        device_uuid: str | None = None, agent_id: str | None = None,
        session_key: str | None = None,
    ) -> SessionSourceWindow:
        if source not in {"mine", "others"} or not 1 <= page_size <= 100:
            raise EngineSessionCursorError("invalid session source window")
        native_mine = source == "mine" and facts.active_engine.strip().casefold() in _NATIVE_MINE_ENGINES
        scope = hashlib.sha256(json.dumps([
            facts.bot_id, facts.owner_id, facts.active_engine, facts.bot_type,
            caller_id, stage, source, page_size, device_uuid, agent_id, session_key,
            "native-mine" if native_mine else "scan",
        ], separators=(",", ":")).encode()).hexdigest()
        offset, returned = _decode(cursor, scope)
        if offset >= MAX_SCAN_DEPTH:
            raise EngineSessionScanDepthError("session source scan depth reached")
        deadline = time.monotonic() + READ_BUDGET_SECONDS
        items: list[dict[str, Any]] = []
        exhausted = False
        lookahead = False
        for _ in range(MAX_READS):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or offset >= MAX_SCAN_DEPTH:
                break
            limit = min(page_size - len(items) + 1, MAX_SCAN_DEPTH - offset)
            params: dict[str, Any] = {"offset": offset, "limit": limit}
            if agent_id:
                params["agent_id"] = agent_id
            if session_key:
                params["session_key"] = session_key
            if native_mine:
                params.update(user_id=caller_id, source="all_but_others")
            # Fallback scans omit user_id/source: an unsupported source may
            # silently narrow the input, and user_id may denote the reader.
            try:
                result = await asyncio.wait_for(self._relay.call(
                    bot_id=facts.bot_id, owner_id=facts.owner_id, facts=facts,
                    stage=stage, method="GET", path="/api/sessions",
                    **({"device_uuid": device_uuid} if device_uuid is not None else {}),
                    params=params, timeout=remaining,
                ), timeout=remaining)
            except TimeoutError:
                raise DeviceAdapterTimeoutError("session source scan timed out") from None
            raw = result.data.get("items") if isinstance(result.data, dict) else result.data
            if (not isinstance(raw, list) or len(raw) > limit
                    or any(not isinstance(row, dict) or not isinstance(row.get("id"), str)
                           or not row["id"] for row in raw)):
                raise EngineUpstreamError("invalid session candidate window")
            for row in raw:
                # Preserve native Owner semantics, including its unknown keys;
                # do not apply a second, different creator parser to mine.
                if native_mine or _matches(row, caller_id, source):
                    if len(items) == page_size:
                        lookahead = True
                        break  # Leave this match for the next window.
                    items.append(row)
                offset += 1
            if lookahead:
                break
            if len(raw) < limit:
                exhausted = True
                break
        has_more = not exhausted
        return SessionSourceWindow(
            items=items, total=returned + len(items), has_more=has_more,
            next_cursor=_encode(offset, returned + len(items), scope) if has_more else None,
            scan_limited=has_more and not lookahead,
        )
