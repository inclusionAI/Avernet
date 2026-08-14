"""Resolve whether a Bot participates in the Skills Pool filesystem contract.

The shared edit guard must not contain engine-specific policy.  This resolver
keeps the conservative default in the domain layer and receives any explicit
engine opt-out from the composition root.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, runtime_checkable

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope


@dataclass(frozen=True, slots=True)
class SkillLayoutParticipation:
    """The layout policy selected for a Bot's current runtime engine."""

    participates_in_pool_layout: bool
    label: str


@runtime_checkable
class SkillLayoutParticipationResolver(Protocol):
    """Port for resolving a Bot's layout participation policy."""

    def resolve(self, *, scope: BotSkillLayoutScope) -> SkillLayoutParticipation: ...


class BotEngineSkillLayoutParticipationResolver:
    """Repository-backed, engine-agnostic implementation of the policy port.

    Unknown, missing, or cross-environment Bot records intentionally resolve to
    the supplied default policy.  Production wiring supplies a participating
    default, so new engines cannot bypass the edit lock accidentally.
    """

    def __init__(
        self,
        *,
        bot_repository: BotRepository,
        default: SkillLayoutParticipation,
        by_engine: Mapping[str, SkillLayoutParticipation],
    ) -> None:
        self._bots = bot_repository
        self._default = default
        self._by_engine = dict(by_engine)

    def resolve(self, *, scope: BotSkillLayoutScope) -> SkillLayoutParticipation:
        bot = self._bots.get_by_id_and_entity(scope.bot_id, scope.entity_id)
        if not bot or bot.get("env") != scope.env:
            return self._default
        engine = bot.get("active_engine")
        if not isinstance(engine, str):
            return self._default
        return self._by_engine.get(engine, self._default)


__all__ = [
    "BotEngineSkillLayoutParticipationResolver",
    "SkillLayoutParticipation",
    "SkillLayoutParticipationResolver",
]
