"""Opt-in caller-relative session listing with bounded fallback scanning."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from agentclaw.community.core.engine_runtime.models import BotFacts

SessionSource = Literal["mine", "others"]


@dataclass(frozen=True)
class SessionSourceWindow:
    items: list[dict[str, Any]]
    total: int
    has_more: bool
    next_cursor: str | None
    scan_limited: bool


@runtime_checkable
class SessionSourceListingProtocol(Protocol):
    async def list_sessions(
        self, *, facts: BotFacts, caller_id: str, stage: str,
        source: SessionSource, page_size: int, cursor: str | None = None,
        device_uuid: str | None = None, agent_id: str | None = None,
        session_key: str | None = None,
    ) -> SessionSourceWindow:
        """Read an already operator-authorized runtime, never another instance.

        ``facts`` must be returned by the relay's operator gate for this caller.
        Mine delegates user_id=caller_id + source=all_but_others to verified
        OpenClaw/AICoding/Claude Code implementations (one filtered read).
        Others and unverified engines use bounded unfiltered scans. Native mine
        preserves the engine's Owner-compatible treatment of unknown keys.
        At most two bounded reads
        and a three-second await budget per request; transport errors propagate.
        Cursors continue native-filtered or fallback raw offsets, bound to
        caller/Bot/stage/filters/size and strategy,
        not snapshots or authority tokens. Reauthorize every continuation.
        ``total`` is the cumulative number of matches returned, a lower bound.
        A short/empty window is not exhaustion: follow ``has_more/next_cursor``.
        Depth beyond 2000 candidates raises, never pretends to be the last page.
        Unrecognised creators remain in mine (legacy owner semantics) and never
        enter others. Membership/task synthetic sessions are not other humans.
        """
        ...
