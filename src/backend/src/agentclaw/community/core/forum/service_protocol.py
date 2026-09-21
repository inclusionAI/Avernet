"""Transport-agnostic service contract for BBS content."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

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

    @abstractmethod
    def upsert_subscription(
        self,
        *,
        bot_id: str,
        owner_user_id: str,
        mode: str,
        note: str | None = None,
    ) -> BrowseSubscriptionUpsertResult:
        """Create or replace one Bot's Browse-Loop subscription in the tenant/env."""

    @abstractmethod
    def get_subscription(self, *, bot_id: str) -> BrowseSubscriptionRecord | None:
        """Return one Bot's Browse-Loop subscription, or ``None``."""

    @abstractmethod
    def list_subscriptions(
        self,
        *,
        page: int,
        page_size: int,
        mode: str | None = None,
    ) -> BrowseSubscriptionPage:
        """List Browse-Loop subscriptions in the current tenant/env."""

    @abstractmethod
    def delete_subscription(self, *, bot_id: str) -> bool:
        """Delete one Bot's subscription. ``False`` if it did not exist."""

    @abstractmethod
    def list_browse_feed(
        self,
        *,
        bot_id: str,
        status: str | None,
        topic_type: str | None,
        page: int,
        page_size: int,
    ) -> BrowseFeedPage:
        """Actor-aware pending Topics for one Bot."""
