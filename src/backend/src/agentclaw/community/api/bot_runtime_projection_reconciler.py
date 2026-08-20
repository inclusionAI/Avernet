"""Service API for applying one Bot's complete capability projection."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from agentclaw.community.core.skills_pool.models import PoolSkillMapping


@runtime_checkable
class BotRuntimeProjectionReconcilerProtocol(Protocol):
    """Apply the complete database desired state for one Bot."""

    async def reconcile(
        self,
        *,
        bot_id: str,
        owner_id: str,
        retired_mappings: Sequence[PoolSkillMapping] = (),
    ) -> None: ...

    async def reconcile_non_skill_projection(
        self,
        *,
        bot_id: str,
        owner_id: str,
    ) -> None:
        """Project MCP/CLI while an external authority owns Skill mappings."""
        ...


__all__ = ["BotRuntimeProjectionReconcilerProtocol"]
