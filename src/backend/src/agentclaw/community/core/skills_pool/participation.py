"""Resolve whether a Bot participates in the Skills Pool filesystem contract.

Pool edit serialization is a property of the Bot's persisted layout state, not
of its engine.  An engine can support Pool while a particular Bot still uses
the Legacy layout, so applying the lock from an engine-level default would
block unrelated Legacy edits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentclaw.community.core.repository.protocols.skills_pool import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from agentclaw.community.core.skills_pool.types import SkillLayout


@dataclass(frozen=True, slots=True)
class SkillLayoutParticipation:
    """The layout policy selected for a Bot's current runtime engine."""

    participates_in_pool_layout: bool
    label: str


@runtime_checkable
class SkillLayoutParticipationResolver(Protocol):
    """Port for resolving a Bot's layout participation policy."""

    def resolve(self, *, scope: BotSkillLayoutScope) -> SkillLayoutParticipation: ...


class BotSkillLayoutStateParticipationResolver:
    """Use the persisted Bot layout as the sole edit-lock participation fact."""

    def __init__(
        self,
        *,
        layout_repository: SkillsPoolLayoutRepositoryProtocol,
    ) -> None:
        self._layouts = layout_repository

    def resolve(self, *, scope: BotSkillLayoutScope) -> SkillLayoutParticipation:
        state = self._layouts.get(scope)
        if (
            state.active_layout is SkillLayout.POOL
            or state.target_layout is SkillLayout.POOL
        ):
            return SkillLayoutParticipation(
                participates_in_pool_layout=True,
                label="pool_layout_state",
            )
        return SkillLayoutParticipation(
            participates_in_pool_layout=False,
            label="legacy_layout_state",
        )


__all__ = [
    "BotSkillLayoutStateParticipationResolver",
    "SkillLayoutParticipation",
    "SkillLayoutParticipationResolver",
]
