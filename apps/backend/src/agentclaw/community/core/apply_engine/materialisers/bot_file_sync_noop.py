"""A no-op ``BotFileSyncPort``: refuses with a clear reason, never syncing.

A stand-in bound until a real per-engine implementation lands: the apply path
through the ``BOT_FILE_SYNC`` materialiser is fully wired (construct → desired
type → materialiser → port), and the only thing missing is the body of
``sync`` itself. Returning ``ok=False`` makes that legible — the report says
"not yet implemented", rather than silently looking like it succeeded — and
keeps the wiring exercisable end to end without a real sync behind it.
"""
from __future__ import annotations

from agentclaw.community.core.ports.bot_file_sync_port import (
    BotFileSyncPort,
    BotFileSyncResult,
    BotFileSyncTarget,
)

#: Singleton reason reused by every call: no per-call state, so it may as well
#: be a constant the report reads verbatim.
_NOT_IMPLEMENTED = BotFileSyncResult(
    ok=False, reason="bot file sync is not yet implemented for this engine"
)


class NoopBotFileSyncPort(BotFileSyncPort):
    """The port with no engine behind it. Replace per engine to make it real."""

    async def sync(
        self,
        *,
        target: BotFileSyncTarget,
        source_bot_id,
        source_owner_id,
        path,
        excludes,
    ):
        return _NOT_IMPLEMENTED
