"""Repository contracts for forum Topic and Post content."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.forum.models import (
        ForumPostPage,
        ForumReplyCreateResult,
        ForumTopicCreateResult,
        ForumTopicPage,
        ForumTopicRecord,
    )


@runtime_checkable
class ForumRepositoryProtocol(Protocol):
    @abstractmethod
    def list_topics(
        self,
        *,
        keyword: str | None,
        status: str | None,
        offset: int,
        limit: int,
        topic_type: str | None = None,
    ) -> ForumTopicPage:
        """List Topics in the current tenant/environment."""

    @abstractmethod
    def list_posts(self, *, topic_id: str, offset: int, limit: int) -> ForumPostPage:
        """List replies after confirming the Topic is visible."""

    @abstractmethod
    def create_topic(
        self,
        *,
        author_type: str,
        author_id: str,
        client_request_id: str,
        title: str,
        body: str,
        topic_type: str = "DISCUSSION",
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
