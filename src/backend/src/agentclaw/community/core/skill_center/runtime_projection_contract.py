"""Core contract for applying one Bot capability projection."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

from agentclaw.community.core.skills_pool.models import PoolSkillMapping


@dataclass(frozen=True)
class ProjectionScope:
    """What one mutation changed, as the mutation itself knows it.

    Declared, never inferred. ``add_mcp`` holds the code it claimed and
    ``remove_mcp`` the one it released, so re-deriving them downstream — by
    diffing a before/after snapshot, say — would be a second source of truth
    for a fact the caller already has, and a second copy of the set-union
    logic that could drift from the projection's own.

    The projector treats ``claimed_mcp`` / ``released_mcp`` as a *guard*
    input, never a source: it intersects them with the projected set, so a
    scope can only ever shrink there. That keeps a single-MCP mutation a
    single device write, and stops a release from deleting a code the
    default policy or a Skill dependency still supplies.
    """

    skills: bool = False
    mcp: bool = False
    claimed_mcp: frozenset[str] = frozenset()
    released_mcp: frozenset[str] = frozenset()
    reconcile: bool = False

    @classmethod
    def everything(cls) -> "ProjectionScope":
        """No mutation to ask, so every projected code counts as claimed.

        Used by the paths with nothing to declare — a device-activated
        restart, a Skill upload — where the device may hold nothing and the
        projection is the whole truth.
        """
        return cls(skills=True, mcp=True, reconcile=True)

    def inverted(self) -> "ProjectionScope":
        """The same scope as a compensating projection would apply it.

        What the forward projection claimed is what an undo releases, and
        vice versa — mirroring how ``retired_logical_skill_mappings`` is
        called with its arguments swapped on the compensating path.
        """
        return replace(
            self,
            claimed_mcp=self.released_mcp,
            released_mcp=self.claimed_mcp,
        )

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
        scope: ProjectionScope = ProjectionScope.everything(),
    ) -> None:
        """Apply the projection, limited to what ``scope`` says changed.

        The default is a full reconcile, so a caller that declares nothing
        keeps the previous whole-set behaviour.
        """
        ...

    async def project_mcp_and_cli(
        self,
        *,
        bot_id: str,
        owner_id: str,
        scope: ProjectionScope = ProjectionScope.everything(),
    ) -> None:
        """Project MCP/CLI while an external authority owns Skill mappings."""
        ...

    async def project_for_cleanup(
        self,
        *,
        bot_id: str,
        owner_id: str,
        scope: ProjectionScope = ProjectionScope.everything(),
    ) -> None:
        """Remove historical capability state through the legacy runtime path."""
        ...


__all__ = ["BotRuntimeProjectorProtocol", "ProjectionScope"]
