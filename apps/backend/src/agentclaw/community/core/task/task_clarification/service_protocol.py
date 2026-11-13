"""Transport-agnostic contract for the task clarification experience pool.

This Protocol is the narrow seam between the experience pool and the rest
of the task closed loop (intake/planner/runner). It owns two methods:

* :meth:`distill`     — offline; consumes raw bot conversation samples and
  returns the distilled experiences. Where the experiences land afterwards
  is the caller's responsibility (this package does not persist them yet).
* :meth:`extract`     — online; returns experiences relevant to the running
  task's goal/context, for callers that opt into the pool.

Implementations live in :mod:`agentclaw.community.core.task.task_clarification`
and are injected at the DI composition root.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.task_clarification.models import (
    ClarificationExperience,
    DistillRequest,
    ExperiencePage,
    ExperienceQuery,
)


@runtime_checkable
class ClarificationExperienceServiceProtocol(Protocol):
    """Narrow seam: offline distillation + online extraction surface."""

    @abstractmethod
    async def distill(
        self,
        request: DistillRequest,
    ) -> tuple[ClarificationExperience, ...]:
        """OFFLINE — distill experiences from bot conversation samples."""

    @abstractmethod
    async def extract(
        self,
        query: ExperienceQuery,
    ) -> ExperiencePage:
        """ONLINE — extract experiences relevant to the running task."""
