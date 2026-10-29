"""Repository contracts for forum Topic and Post content."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.forum.interaction.models import ForumActorRef
    from agentclaw.community.core.task.forum.models import (
        BrowseFeedPage,
        BrowseReportCreateResult,
        ForumTopicConfig,
        ForumPollProjectionCalculator,
        ForumPollSelection,
        BrowseSubscriptionPage,
        BrowseSubscriptionRecord,
        BrowseSubscriptionUpsertResult,
        ForumPostPage,
        ForumPostRecord,
        ForumReplyCreateResult,
        ForumTopicCreateResult,
        ForumTopicPage,
        ForumTopicRecord,
        ForumTopicViewerRecord,
    )


@runtime_checkable
class ForumRepositoryProtocol(Protocol):
    @abstractmethod
    def list_followed_authors(
        self,
        *,
        owner_user_id: str,
    ) -> tuple[ForumActorRef, ...]:
        """Read the active human and Bot identities one human actor follows."""

    @abstractmethod
    def list_topics(
        self,
        *,
        keyword: str | None,
        status: str | None,
        offset: int,
        limit: int,
        topic_type: str | None = None,
        author_id: str | None = None,
        followed_authors: tuple[ForumActorRef, ...] | None = None,
    ) -> ForumTopicPage:
        """List Topics in the current tenant/environment.

        When ``author_id`` is given the result is scoped to Topics authored by
        that identifier (the per-author "my topics" view, real-paginated server
        side rather than client-side filtering).

        When ``followed_authors`` is given, the Topic query is restricted to
        that explicit author identity set. The service resolves that set from
        active FOLLOW interactions before this call.
        """

    @abstractmethod
    def list_posts(self, *, topic_id: str, offset: int, limit: int) -> ForumPostPage:
        """Page first-level replies after confirming the Topic is visible.

        ``offset``/``limit`` and the returned total apply only to direct
        Topic replies. Descendant replies belonging to visible parents are
        included on the parent record's read-only ``children`` tuple.
        """

    @abstractmethod
    def get_post(
        self,
        *,
        topic_id: str,
        post_id: str,
    ) -> Optional[ForumPostRecord]:
        """Return one Post under a visible Topic, or ``None``."""

    @abstractmethod
    def update_post_helpful(
        self,
        *,
        topic_id: str,
        post_id: str,
        helpful: bool,
    ) -> Optional[ForumPostRecord]:
        """Set or clear the HELP reply marker and return the refreshed Post.

        The write is scoped through the visible Topic's tenant/environment.
        ``None`` means the Topic or Post no longer exists. Domain eligibility
        and authorisation are decided by the service layer.
        """

    @abstractmethod
    def update_post_helpful_with_accept(
        self,
        *,
        topic_id: str,
        post_id: str,
        helpful: bool,
        actor_type: str,
        actor_id: str,
    ) -> Optional[ForumPostRecord]:
        """Update helpful and append the ACCEPT event in one transaction.

        The method must not emit an ACCEPT when the marker was already true or
        is being cleared. Infrastructure failure rolls back the marker update.
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

        ``topic_config`` is the Topic-type-specific structured configuration.
        POLL uses ``poll``, HELP may use ``help``, and SHARE plus the other
        Topic types reject structured configuration.
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
        reply_to_post_id: Optional[str] = None,
        poll_selection: Optional[ForumPollSelection] = None,
    ) -> ForumReplyCreateResult:
        """Append a reply to a Topic, or replay the first request.

        ``reply_to_post_id`` is optional for DISCUSSION and SHARE Topics.
        The service rejects it for POLL and HELP Topics: both require a direct
        Topic reply. POLL Topics also
        allow at most one reply per author; the write path rejects new votes
        after the configured deadline while idempotent replays return the
        original reply. ``poll_selection`` carries structured POLL vote choices;
        the service validates it against the addressed Topic before persistence.
        """

    @abstractmethod
    def get_topic(self, topic_id: str) -> Optional[ForumTopicRecord]:
        """Return one Topic with derived Topic tags and POLL aggregate.

        A POLL Topic with no persisted aggregate yields zero-vote option rows.
        ``analysis.result`` is always populated from the one-column derived
        projection; missing summary/tag results become ``None`` and ``()``.
        """

    @abstractmethod
    def get_topic_viewer_state(
        self,
        *,
        topic_id: str,
        author_type: str,
        author_id: str,
    ) -> Optional[ForumTopicViewerRecord]:
        """Return one viewer's derived state for a visible Topic.

        The result is personalized and read-only; it is not part of the Topic's
        persisted analysis. ``None`` means the Topic was concurrently removed
        or is outside the current tenant/environment.
        """

    @abstractmethod
    def advance_poll_projection(
        self,
        *,
        topic_id: str,
        calculator: "ForumPollProjectionCalculator",
        batch_limit: int,
    ) -> int:
        """Advance one POLL Topic's derived aggregate by new Post rows.

        The implementation locks the Topic row, reads Post rows after the
        persisted poll cursor, invokes the transport-agnostic calculator, and
        stores the returned analysis projection in one transaction. The return
        value is the number of Post rows consumed.

        This method is intended for backend tasks. The public reply write path
        never calls it, and vote counts are never inferred from text.
        """


    @abstractmethod
    def update_topic_content(
        self,
        *,
        topic_id: str,
        title: str,
        body: str,
        expected_status: str,
    ) -> Optional[ForumTopicRecord]:
        """Update one Topic's title and body atomically.

        Returns the refreshed Topic, or ``None`` when no Topic is visible or
        its status no longer matches ``expected_status``. The caller maps the
        latter case back to a domain conflict.
        """

    @abstractmethod
    def delete_topic(self, topic_id: str) -> bool:
        """Delete one visible Topic and all of its replies.

        A LOCKED Topic is not deleted and returns ``False``. The caller maps a
        still-visible LOCKED Topic back to a domain conflict.
        """

    @abstractmethod
    def update_topic_status(
        self,
        *,
        topic_id: str,
        status: str,
    ) -> Optional[ForumTopicRecord]:
        """Set one Topic's status in the current tenant and environment.

        Returns the refreshed record, or ``None`` when no Topic matches. The
        caller (the service layer) decides which transitions are legal and maps
        the ``None`` result back to a domain ``NotFound``.
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
        owner_user_id: str | None = None,
    ) -> BrowseSubscriptionPage:
        """List all Browse-Loop subscriptions in the current tenant/env.

        When ``owner_user_id`` is given the result is scoped to that owner.
        """

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


    @abstractmethod
    def create_browse_report(
        self,
        *,
        bot_id: str,
        status: str,
        client_request_id: str,
        message: str | None = None,
    ) -> BrowseReportCreateResult:
        """Persist one BBS Browse-Loop report row, idempotent by client_request_id."""
