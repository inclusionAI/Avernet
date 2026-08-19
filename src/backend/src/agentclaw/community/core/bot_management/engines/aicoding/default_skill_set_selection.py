"""Default SkillSet compatibility contributed by the aicoding engine."""

from __future__ import annotations

from agentclaw.community.core.skill_center.policies.default_skill_set_selection import (
    DefaultSkillSetSelection,
)

from .strategy import AICODING_ENGINE_TYPE, CLAUDE_CODE_ENGINE_TYPE


class AicodingDefaultSkillSetSelectionResolver:
    """Route non-normal Claude Code default SkillSet reads to aicoding rows.

    Routed Claude Code should first use the current bot-scoped persisted
    default, then fall back to the existing online global default SkillSet rows
    created under the aicoding runtime engine and ``bolt_id='default'``.  Only
    this engine relationship needs the compatibility lookup; all unclaimed
    inputs fall back to the persisted engine in the neutral policy.
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
                        bolt_id=self._GLOBAL_DEFAULT_BOLT_ID,
                    ),
                    DefaultSkillSetSelection(
                        engine_type=persisted_engine_type,
                        bolt_id=self._GLOBAL_DEFAULT_BOLT_ID,
                    ),
                )
            )
            return tuple(candidates)
        return None


__all__ = ["AicodingDefaultSkillSetSelectionResolver"]
