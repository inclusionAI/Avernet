"""ONLINE — extract clarification experiences relevant to the running task.

Given an :class:`ExperienceQuery`, returns an :class:`ExperiencePage` of
experiences that should inform clarification at the current node. This module
implements the online half of
:class:`ClarificationExperienceServiceProtocol`.

It is the only entry point callers in the task closed loop
(intake/planner/runner) should depend on; they never touch the offline
distillation half directly.
"""
from __future__ import annotations

from agentclaw.community.core.task.task_clarification.models import (
    ExperiencePage,
    ExperienceQuery,
)


class ExtractionService:
    """Online experience extraction (placeholder).

    The class structure is wired so the implementation step only needs to fill
    the body. The backing store for distilled experiences is injected later
    (this package is intentionally persistence-agnostic at the architecture
    step).
    """

    async def extract(
        self,
        query: ExperienceQuery,
    ) -> ExperiencePage:
        raise NotImplementedError(
            "ExtractionService.extract is a placeholder; fill the body in the "
            "implementation step."
        )
