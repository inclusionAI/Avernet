"""Transport-agnostic service contract for general user feedback."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.feedback.models import (
    FeedbackCreateResult,
    FeedbackPage,
)


@runtime_checkable
class FeedbackServiceProtocol(Protocol):
    @abstractmethod
    def create_feedback(
        self,
        *,
        reporter_id: str,
        module: str,
        content: str,
    ) -> FeedbackCreateResult:
        """Persist one feedback entry. ``created`` is always ``True`` here:
        feedback has no idempotency key, so every call is a new row."""

    @abstractmethod
    def list_feedback(
        self,
        *,
        module: str | None,
        reporter_id: str | None,
        page: int,
        page_size: int,
    ) -> FeedbackPage:
        """Page feedback in the current tenant/environment, newest first.

        Both filters are optional and combine with AND.
        """
