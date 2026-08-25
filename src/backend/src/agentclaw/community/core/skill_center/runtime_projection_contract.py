"""Core contract for applying one Bot capability projection."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from agentclaw.community.core.skills_pool.models import PoolSkillMapping


@runtime_checkable
class BotRuntimeProjectionReconcilerProtocol(Protocol):
    """Apply database desired state through the selected runtime authority.

    Every consumer of this contract is a Core service, so the contract lives
    here rather than in ``api/``: Core must not import ``api/`` (Rule 6, gated
    by ``test_core_layer_does_not_import_api``). Members are ``@abstractmethod``
    so the implementation that inherits this Protocol fails at construction
    naming a dropped member, instead of inheriting a silent ``...`` stub.
    """

    @abstractmethod
    async def snapshot_skill_mappings(
        self,
        *,
        bot_id: str,
        owner_id: str,
    ) -> tuple[PoolSkillMapping, ...]:
        """Return the current desired Skill mappings without publishing them."""
        ...

    @abstractmethod
    async def reconcile(
        self,
        *,
        bot_id: str,
        owner_id: str,
        retired_mappings: Sequence[PoolSkillMapping] = (),
    ) -> None: ...

    @abstractmethod
    async def reconcile_non_skill_projection(
        self,
        *,
        bot_id: str,
        owner_id: str,
    ) -> None:
        """Project MCP/CLI while an external authority owns Skill mappings."""
        ...

    @abstractmethod
    async def reconcile_cleanup(
        self,
        *,
        bot_id: str,
        owner_id: str,
    ) -> None:
        """Remove historical capability state through the legacy runtime path."""
        ...


__all__ = ["BotRuntimeProjectionReconcilerProtocol"]
