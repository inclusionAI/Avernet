"""Core contract for applying one Bot capability projection."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from agentclaw.community.core.skills_pool.models import PoolSkillMapping


@runtime_checkable
class BotRuntimeProjectorProtocol(Protocol):
    """Apply database desired state through the selected runtime authority."""

    async def snapshot_skill_mappings(
        self,
        *,
        bot_id: str,
        owner_id: str,
    ) -> tuple[PoolSkillMapping, ...]:
        """Return the current desired Skill mappings without publishing them."""
        ...

    async def project(
        self,
        *,
        bot_id: str,
        owner_id: str,
        retired_mappings: Sequence[PoolSkillMapping] = (),
    ) -> None: ...

    async def project_mcp_and_cli(
        self,
        *,
        bot_id: str,
        owner_id: str,
    ) -> None:
        """Project MCP/CLI while an external authority owns Skill mappings."""
        ...

    async def project_for_cleanup(
        self,
        *,
        bot_id: str,
        owner_id: str,
    ) -> None:
        """Remove historical capability state through the legacy runtime path."""
        ...


__all__ = ["BotRuntimeProjectorProtocol"]
