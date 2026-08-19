"""Default SkillSet selection contracts.

Skill Center owns the neutral query shape for default SkillSets, but concrete
engine compatibility quirks are contributed through resolvers registered by the
engine composition root.  This keeps ``core/skill_center`` free from engine-name
branching while still allowing existing data-layout compatibility rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol


@dataclass(frozen=True, slots=True)
class DefaultSkillSetSelection:
    """Neutral constraints used when querying default SkillSets."""

    engine_type: str | None
    bolt_id: str | None = None


def normalize_engine_for_default_skill_set(engine_type: str | None) -> str:
    """Normalize public engine spelling for resolver matching."""

    return (engine_type or "").strip().lower().replace("-", "_")


class DefaultSkillSetSelectionResolver(Protocol):
    """Engine-contributed default SkillSet compatibility hook.

    Return ``None`` when the resolver does not own the input.  The policy then
    tries the next resolver and finally falls back to the persisted engine.
    """

    def resolve_default_skill_set_selection(
        self,
        *,
        persisted_engine_type: str | None,
        runtime_engine_type: str | None,
        normalized_persisted_engine_type: str,
        normalized_runtime_engine_type: str,
    ) -> tuple[DefaultSkillSetSelection, ...] | DefaultSkillSetSelection | None:
        """Return default SkillSet lookup candidates, or ``None``."""


class DefaultSkillSetSelectionPolicy:
    """Resolve default SkillSet query constraints through registered hooks."""

    def __init__(
        self,
        resolvers: Iterable[DefaultSkillSetSelectionResolver] | None = None,
    ) -> None:
        self._resolvers = tuple(resolvers or ())

    def resolve(
        self,
        *,
        persisted_engine_type: str | None,
        runtime_engine_type: str | None,
    ) -> DefaultSkillSetSelection:
        return self.resolve_candidates(
            persisted_engine_type=persisted_engine_type,
            runtime_engine_type=runtime_engine_type,
        )[0]

    def resolve_candidates(
        self,
        *,
        persisted_engine_type: str | None,
        runtime_engine_type: str | None,
    ) -> tuple[DefaultSkillSetSelection, ...]:
        normalized_persisted_engine = normalize_engine_for_default_skill_set(
            persisted_engine_type
        )
        normalized_runtime_engine = normalize_engine_for_default_skill_set(
            runtime_engine_type
        )
        for resolver in self._resolvers:
            selection = resolver.resolve_default_skill_set_selection(
                persisted_engine_type=persisted_engine_type,
                runtime_engine_type=runtime_engine_type,
                normalized_persisted_engine_type=normalized_persisted_engine,
                normalized_runtime_engine_type=normalized_runtime_engine,
            )
            if isinstance(selection, DefaultSkillSetSelection):
                return (selection,)
            if selection is not None:
                return selection
        return (DefaultSkillSetSelection(engine_type=persisted_engine_type),)


__all__ = [
    "DefaultSkillSetSelection",
    "DefaultSkillSetSelectionPolicy",
    "DefaultSkillSetSelectionResolver",
    "normalize_engine_for_default_skill_set",
]
