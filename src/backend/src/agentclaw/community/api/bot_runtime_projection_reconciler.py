"""Public Service API for Bot runtime projection reconciliation."""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectionReconcilerProtocol as CoreBotRuntimeProjectionReconcilerProtocol,
)
from agentclaw.community.core.skills_pool.models import PoolSkillMapping


@runtime_checkable
class BotRuntimeProjectionReconcilerProtocol(
    CoreBotRuntimeProjectionReconcilerProtocol, Protocol
):
    """Transport-facing contract; Core depends only on its sibling contract."""

    async def snapshot_skill_mappings(
        self,
        *,
        bot_id: str,
        owner_id: str,
    ) -> tuple[PoolSkillMapping, ...]: ...

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
    ) -> None: ...

    async def reconcile_cleanup(
        self,
        *,
        bot_id: str,
        owner_id: str,
    ) -> None: ...


__all__ = ["BotRuntimeProjectionReconcilerProtocol"]
