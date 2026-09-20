"""Transport-agnostic service contract for BBS content writes."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.forum.models import (
    ForumReplyCreateResult,
    ForumTopicCreateResult,
)


@runtime_checkable
class ForumServiceProtocol(Protocol):
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
