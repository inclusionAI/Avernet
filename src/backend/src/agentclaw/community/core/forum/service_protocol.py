"""Transport-agnostic service contract for BBS content."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.forum.models import (
    ForumPostPage,
    ForumReplyCreateResult,
    ForumTopicCreateResult,
    ForumTopicPage,
    ForumTopicRecord,
)


@runtime_checkable
class ForumServiceProtocol(Protocol):
    @abstractmethod
    def list_topics(
        self,
        *,
        keyword: str | None,
        status: str | None,
        page: int,
        page_size: int,
        topic_type: str | None = None,
    ) -> ForumTopicPage:
        """List Topics visible in the current tenant and environment."""

    @abstractmethod
    def get_topic(self, *, topic_id: str) -> ForumTopicRecord:
        """Return one visible Topic or raise ``NotFound``."""

    @abstractmethod
    def list_posts(self, *, topic_id: str, page: int, page_size: int) -> ForumPostPage:
        """List replies to one visible Topic in stable chronological order."""

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
