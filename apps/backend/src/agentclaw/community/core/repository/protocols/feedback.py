"""Repository contract for general user feedback."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.feedback.models import (
        FeedbackCreateResult,
        FeedbackPage,
    )


@runtime_checkable
class FeedbackRepositoryProtocol(Protocol):
    @abstractmethod
    def create_feedback(
        self,
        *,
        reporter_id: str,
        module: str,
        content: str,
    ) -> FeedbackCreateResult:
        """Persist one feedback entry in the current tenant/environment."""

    @abstractmethod
    def list_feedback(
        self,
        *,
        module: str | None,
        reporter_id: str | None,
        offset: int,
        limit: int,
    ) -> FeedbackPage:
        """Page feedback in the current tenant/environment, newest first."""
