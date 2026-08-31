"""Service API for resolving the current Engine runtime Skills layout."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    RuntimeLayoutProbeResult,
    RuntimeLayoutProbeStatus,
)


@runtime_checkable
class RuntimeLayoutProbeServiceProtocol(Protocol):
    """Ask the active Engine runtime to resolve its authoritative layout."""

    async def probe_bot(
        self,
        *,
        bot_id: str,
        user_id: str,
        engine: str,
    ) -> RuntimeLayoutProbeResult: ...


__all__ = [
    "RuntimeLayoutProbeResult",
    "RuntimeLayoutProbeServiceProtocol",
    "RuntimeLayoutProbeStatus",
]
