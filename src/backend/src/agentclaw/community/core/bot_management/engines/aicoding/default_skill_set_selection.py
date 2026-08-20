"""aicoding default SkillSet selection compatibility."""

from __future__ import annotations

from agentclaw.community.core.skill_center.policies.default_skill_set_selection import (
    DefaultSkillSetSelection,
)

from .strategy import AICODING_ENGINE_TYPE, CLAUDE_CODE_ENGINE_TYPE


class AicodingDefaultSkillSetSelectionResolver:
    """Route non-normal Claude Code default SkillSet reads to aicoding rows.

    Routed Claude Code first uses the current bot-scoped persisted default, then
    falls back to the legacy global default SkillSet rows. In current data these
    global defaults are stored with ``bolt_id IS NULL`` for both aicoding and
    claude_code; there is no ``aicoding + bolt_id='default'`` row to query.
    """

    _GLOBAL_DEFAULT_BOLT_ID = "default"

    def resolve_default_skill_set_selection(
        self,
        *,
        persisted_engine_type: str | None,
        runtime_engine_type: str | None,
        normalized_persisted_engine_type: str,
        normalized_runtime_engine_type: str,
        bolt_id: str | None = None,
    ) -> tuple[DefaultSkillSetSelection, ...] | None:
        if (
            normalized_persisted_engine_type == CLAUDE_CODE_ENGINE_TYPE
            and normalized_runtime_engine_type == AICODING_ENGINE_TYPE
        ):
            candidates: list[DefaultSkillSetSelection] = []
            if bolt_id and bolt_id != self._GLOBAL_DEFAULT_BOLT_ID:
                candidates.append(
                    DefaultSkillSetSelection(
                        engine_type=persisted_engine_type,
                        bolt_id=bolt_id,
                    )
                )
            candidates.extend(
                (
                    DefaultSkillSetSelection(
                        engine_type=runtime_engine_type,
                        bolt_id=None,
                    ),
                    DefaultSkillSetSelection(
                        engine_type=persisted_engine_type,
                        bolt_id=None,
                    ),
                )
            )
            return tuple(candidates)
        return None


__all__ = ["AicodingDefaultSkillSetSelectionResolver"]
