"""Repository contracts for forum Topic and Post content."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.forum.models import (
        BrowseFeedPage,
        BrowseSubscriptionPage,
        BrowseSubscriptionRecord,
        BrowseSubscriptionUpsertResult,
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

    @abstractmethod
    def upsert_subscription(
        self,
        *,
        bot_id: str,
        owner_user_id: str,
        mode: str,
        note: str | None = None,
    ) -> BrowseSubscriptionUpsertResult:
        """Create or replace one Bot's Browse-Loop subscription (idempotent)."""

    @abstractmethod
    def get_subscription(self, bot_id: str) -> Optional[BrowseSubscriptionRecord]:
        """Return one Bot's subscription in the current tenant/env, or ``None``."""

    @abstractmethod
    def list_subscriptions(
        self,
        *,
        offset: int,
        limit: int,
        mode: str | None = None,
    ) -> BrowseSubscriptionPage:
        """List all Browse-Loop subscriptions in the current tenant/env."""

    @abstractmethod
    def delete_subscription(self, bot_id: str) -> bool:
        """Delete one Bot's subscription. Return ``False`` if it did not exist."""

    @abstractmethod
    def list_browse_feed(
        self,
        *,
        bot_id: str,
        status: str | None,
        topic_type: str | None,
        offset: int,
        limit: int,
    ) -> BrowseFeedPage:
        """Actor-aware pending Topics for one Bot in the current tenant/env.

        POLL/NOTICE where the actor already replied are excluded; DISCUSSION
        are always returned. Each row carries ``my_reply_count``.
        """
