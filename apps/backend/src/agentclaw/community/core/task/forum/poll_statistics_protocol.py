"""Transport-agnostic Service API contract for deterministic poll statistics."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.forum.models import (
    ForumPollStatisticsResult,
)


@runtime_checkable
class ForumPollStatisticsServiceProtocol(Protocol):
    @abstractmethod
    def execute(self) -> ForumPollStatisticsResult:
        """Project all new POLL Topic votes using exact stored selections.

        Text reply bodies are never parsed or interpreted as votes, and this
        contract deliberately has no model-output input. The term model here
        refers to the persisted read projection, not an LLM.
        """


__all__ = ["ForumPollStatisticsServiceProtocol"]
