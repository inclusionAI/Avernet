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

    #: Project the Skill half — publish mappings, or sync the legacy runtime.
    skills: bool = False
    #: Project the MCP half — device delivery, the allow-list, and Passport.
    mcp: bool = False
    #: MCP codes this mutation added to the Bot. Configuration is pushed for
    #: these and no others, so a one-MCP add stays a one-MCP device write.
    claimed_mcp: frozenset[str] = frozenset()
    #: MCP codes this mutation took away. Configuration is deleted for these
    #: and no others.
    released_mcp: frozenset[str] = frozenset()
    #: Treat *every* projected MCP code as claimed, ignoring ``claimed_mcp``.
    #:
    #: Only for callers with no delta to name — a device-activated restart or
    #: a Skill upload — where the device may hold no configuration at all and
    #: the projection is the whole truth. They cannot fill ``claimed_mcp``
    #: because they do not know the projected set until the projector resolves
    #: it, which is why this is a flag and not a code set.
    #:
    #: It says nothing about which halves run: ``skills`` and ``mcp`` decide
    #: that on their own. An MCP-only reconcile — ``ProjectionScope(mcp=True,
    #: claim_all_mcp=True)`` — is a real and used shape.
    #:
    #: It never deletes: ``released_mcp`` is not consulted when this is set,
    #: so a reconcile can only ever add configuration to a device.
    claim_all_mcp: bool = False

    @classmethod
    def everything(cls) -> "ProjectionScope":
        """Both halves, with every projected MCP code counted as claimed.

        The shape for callers that have no mutation to describe: a
        device-activated restart, a Skill upload. See ``claim_all_mcp``.
        """
        return cls(skills=True, mcp=True, claim_all_mcp=True)

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
        scope: ProjectionScope,
    ) -> None:
        """Apply the projection, limited to what ``scope`` says changed.

        ``scope`` is required rather than defaulted: a caller that forgot it
        would silently get a full reconcile, which is the expensive answer and
        never the one a mutation wants. Callers with genuinely nothing to
        declare say so with ``ProjectionScope.everything()``.
        """
        ...

    async def project_mcp_and_cli(
        self,
        *,
        bot_id: str,
        owner_id: str,
        scope: ProjectionScope,
    ) -> None:
        """Project MCP/CLI while an external authority owns Skill mappings."""
        ...

    async def project_for_cleanup(
        self,
        *,
        bot_id: str,
        owner_id: str,
        scope: ProjectionScope,
    ) -> None:
        """Remove historical capability state through the legacy runtime path."""
        ...


__all__ = ["BotRuntimeProjectorProtocol", "ProjectionScope"]
