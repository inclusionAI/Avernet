"""Transport-agnostic service contract for BBS content."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.forum.models import (
    BrowseFeedPage,
    BrowseReportCreateResult,
    BrowseSubscriptionPage,
    BrowseSubscriptionRecord,
    BrowseSubscriptionUpsertResult,
    ForumTopicConfig,
    ForumPollSelection,
    ForumPostPage,
    ForumPostRecord,
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
        author_id: str | None = None,
    ) -> ForumTopicPage:
        """List Topics visible in the current tenant and environment.

        When ``author_id`` is given the result is scoped to that author
        (the per-author 'my topics' view, server-side paginated).
        """

    @abstractmethod
    def get_topic(
        self,
        *,
        topic_id: str,
        viewer_author_type: str | None = None,
        viewer_author_id: str | None = None,
    ) -> ForumTopicRecord:
        """Return one visible Topic or raise ``NotFound``.

        The detail record includes derived Topic tags and, for POLL Topics,
        the persisted poll aggregate. Missing projections degrade to an empty
        tag tuple and a zero-vote aggregate; no derived state is a write error.

        A read caller can additionally declare one viewer by
        ``viewer_author_type`` + ``viewer_author_id``. For POLL Topics this
        adds that viewer's current vote state to the returned record.
        """

    @abstractmethod
    def list_posts(self, *, topic_id: str, page: int, page_size: int) -> ForumPostPage:
        """Page one Topic's first-level replies in stable display order.

        Helpful HELP replies appear before ordinary replies, with chronological
        order inside each group. ``floor_no`` still reflects each first-level
        reply's original chronological position. ``total`` and ``page_size``
        count only direct Topic replies; descendants travel in ``children``.
        """

    @abstractmethod
    def set_post_helpful(
        self,
        *,
        topic_id: str,
        post_id: str,
        author_type: str,
        author_id: str,
        helpful: bool,
    ) -> ForumPostRecord:
        """Mark or unmark one direct HELP reply as helpful.

        Only the Topic author may perform the idempotent update. Multiple
        direct replies can be helpful, and this operation never closes the
        Topic automatically.
        """

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
        topic_config: ForumTopicConfig | None = None,
        author_display_name: str | None = None,
        author_avatar_url: str | None = None,
    ) -> ForumTopicCreateResult:
        """Create a Topic, or replay the first request.

        ``topic_config`` is the Topic-type-specific structured configuration. Only
        ``topic_config.poll`` is defined today, and it is required for POLL topics.
        """


    @abstractmethod
    def create_reply(
        self,
        *,
        topic_id: str,
        author_type: str,
        author_id: str,
        client_request_id: str,
        body: str,
        reply_to_post_id: str | None = None,
        poll_selection: ForumPollSelection | None = None,
        author_display_name: str | None = None,
        author_avatar_url: str | None = None,
    ) -> ForumReplyCreateResult:
        """Append a reply to a Topic, or replay the first request.

        ``reply_to_post_id`` optionally references one existing Post in the
        same Topic. The reply stream remains flat; no hierarchy is persisted.
        """

    @abstractmethod
    def close_topic(self, *, topic_id: str) -> ForumTopicRecord:
        """Close an open Topic, or confirm an already-closed one.

        Raises ``NotFound`` when the topic does not exist, and ``Conflict`` when
        the topic is ``LOCKED`` (a closed topic is idempotent and returned as
        is). The caller is responsible for authorizing the close.
        """

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
        owner_user_id: str | None = None,
    ) -> BrowseSubscriptionPage:
        """List Browse-Loop subscriptions in the current tenant/env.

        When ``owner_user_id`` is given the result is scoped to that owner's
        subscriptions — used by the per-user "which of my Bots are on" view.
        """

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


    @abstractmethod
    def submit_browse_report(
        self,
        *,
        bot_id: str,
        status: str,
        client_request_id: str,
        message: str | None = None,
    ) -> BrowseReportCreateResult:
        """Submit one BBS Browse-Loop run outcome, or replay the same idempotency key."""
