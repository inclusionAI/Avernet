"""OFFLINE — distill clarification experiences from bot conversation samples.

Reads bot conversation/session samples via
:class:`BotSessionReader`, applies an (LLM-based) distillation step and
emits :class:`ClarificationExperience` records. This module implements the
offline half of
:class:`ClarificationExperienceServiceProtocol`.

It does not own persistence: the caller (typically a scheduled job or a CLI
entrypoint) decides where the returned experiences land.
"""
from __future__ import annotations

from agentclaw.community.core.task.task_clarification.bot_session_reader import (
    BotSessionReader,
)
from agentclaw.community.core.task.task_clarification.models import (
    ClarificationExperience,
    DistillRequest,
)


class DistillationService:
    """Offline experience distillation (placeholder).

    The class structure is wired so the implementation step only needs to fill
    the body. The reader and any LLM/prompts dependency are injected, keeping
    this object transport-agnostic and unit-testable with stubs.
    """

    def __init__(self, reader: BotSessionReader) -> None:
        self._reader = reader

    async def distill(
        self,
        request: DistillRequest,
    ) -> tuple[ClarificationExperience, ...]:
        raise NotImplementedError(
            "DistillationService.distill is a placeholder; fill the body in the "
            "implementation step."
        )
