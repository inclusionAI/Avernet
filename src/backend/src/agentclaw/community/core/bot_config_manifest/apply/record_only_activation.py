"""Activation that records and does not project (W8, teclaw platform-managed).

``DirectActivationService`` writes the desired state and then projects it to
the running container. On the platform-managed teclaw path the projection is
the artifact — the composer reads the same rows — and the strategy closes the
apply with one whole-artifact redeliver, so a per-mutation projection would
be one extra delivery per skill and per MCP server, each of them redundant
and each of them a container round-trip an apply that needs no container
must not make. This wrapper keeps the service's surface and passes
``project=False`` on every write; the reads it forwards unchanged.
"""
from __future__ import annotations

from typing import Any


class RecordOnlyActivation:
    """``DirectActivationService`` with projection off, same surface."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    # ── reads, unchanged ─────────────────────────────────────────────────

    def list_installed_mcps(self, *, bot_id: str, owner_id: str, actor_id: str) -> set[str]:
        return self._inner.list_installed_mcps(
            bot_id=bot_id, owner_id=owner_id, actor_id=actor_id
        )

    def platform_default_mcp_codes(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> frozenset[str]:
        return self._inner.platform_default_mcp_codes(
            bot_id=bot_id, owner_id=owner_id, actor_id=actor_id
        )

    # ── writes, record only ──────────────────────────────────────────────

    async def activate_mcp(
        self, *, server_code: str, bot_id: str, owner_id: str, actor_id: str
    ) -> dict[str, Any]:
        return await self._inner.activate_mcp(
            server_code=server_code, bot_id=bot_id, owner_id=owner_id,
            actor_id=actor_id, project=False,
        )

    async def deactivate_mcp(
        self, *, server_code: str, bot_id: str, owner_id: str, actor_id: str
    ) -> dict[str, Any]:
        return await self._inner.deactivate_mcp(
            server_code=server_code, bot_id=bot_id, owner_id=owner_id,
            actor_id=actor_id, project=False,
        )

    async def activate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str
    ) -> dict[str, Any]:
        return await self._inner.activate_skill(
            skill_id=skill_id, bot_id=bot_id, owner_id=owner_id,
            actor_id=actor_id, project=False,
        )

    async def deactivate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str
    ) -> dict[str, Any]:
        return await self._inner.deactivate_skill(
            skill_id=skill_id, bot_id=bot_id, owner_id=owner_id,
            actor_id=actor_id, project=False,
        )


__all__ = ["RecordOnlyActivation"]
