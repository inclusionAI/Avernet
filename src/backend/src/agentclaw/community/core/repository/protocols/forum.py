"""Repository contracts for phase-1 forum writes and minimal reads."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.forum.models import (
        ForumReplyCreateResult,
        ForumTopicCreateResult,
        ForumTopicRecord,
    )


@runtime_checkable
class ForumRepositoryProtocol(Protocol):
    @abstractmethod
    def create_topic(
        self,
        *,
        author_type: str,
        author_id: str,
        client_request_id: str,
        title: str,
        body: str,
    ) -> ForumTopicCreateResult:
        """Create a Topic, or replay the first request."""

    @abstractmethod
    def create_reply(
        self,
        *,
        topic_id: str,
        author_type: str,
        author_id: str,
        client_request_id: str,
        body: str,
    ) -> ForumReplyCreateResult:
        """Append a reply to a Topic, or replay the first request."""

    @abstractmethod
    def get_topic(self, topic_id: str) -> Optional[ForumTopicRecord]:
        """Return one Topic in the current tenant/environment, or ``None``."""
