"""Protocol bridge — avoids core → api import violation.

``core/`` cannot import from ``api/``, so we define local Protocols
for notification dispatch and session lifecycle management.
"""
from __future__ import annotations

from typing import Any, Protocol

from typing import ContextManager


class SessionProvider(Protocol):
    """Minimal session lifecycle provider for service-layer transaction boundaries.

    Services need ``orm_session()`` to manage transaction boundaries but
    should NOT depend on the full ``DatabasePlugin`` protocol.  This
    Protocol narrows the dependency to the single method actually used.

    ``DatabasePlugin`` naturally satisfies this protocol via structural
    subtyping — no adapter needed.  In DI wiring, ``db: DatabasePlugin``
    is passed directly as the ``db`` argument.

    Implements:
      - Rule 20: ``local/`` (SQLite DatabasePlugin) and ``prod/`` (corp
        DatabasePlugin) implementations already exist.
      - Rule 21: ``NoopSessionProvider`` / ``MockSessionProvider`` can be
        provided for testing (see ``tests/contracts/``).
    """

    def orm_session(self) -> ContextManager[Any]:
        """Yield a SQLAlchemy Session; commit on clean exit, rollback on exception."""
        ...


class GovernanceNotifySender(Protocol):
    """Protocol for sending governance notifications via external channels.

    Phase 1 supports two interchangeable channels:
      - **Markdown** (``send_markdown``): DingTalk ``batchSend``
        (sampleMarkdown) — simple notification with deep-link.
      - **TC Card** (``send_tc_card``): DingTalk ``createAndDeliver``
        — card shell with Markdown reason + detailLink deep-link
        that opens a teamclaw preview iframe for full detail and feedback.

    Channel selection is driven by ``EconomyGovernanceConfig.notify_channel``.
    When TC card credentials are missing, auto-degrades to Markdown.

    Implementations:
      - ``DingTalkMarkdownSender`` (prod) — real DingTalk batchSend API call
      - ``DingTalkTcCardSender`` (prod) — real DingTalk createAndDeliver API call

    All methods must **never raise** — errors are caught internally
    and logged, returning ``None`` instead.
    """

    def send_markdown(
        self,
        user_id: str,
        title: str,
        content: str,
    ) -> str | None:
        """Send a Markdown notification via DingTalk batchSend.

        Args:
            user_id: Recipient staffId (maps to DingTalk ``userIds[]``).
            title: Message title (DingTalk ``sampleMarkdown.title``).
            content: Markdown body (DingTalk ``sampleMarkdown.text``).

        Returns:
            External message ID on success, ``None`` on failure.
        """
        ...

    def send_tc_card(
        self,
        user_id: str,
        reason: str,
        detail_link: str,
        bot_id: str,
        card_id: str,
        notification_data: dict[str, Any],
        out_track_id_prefix: str = "dingtalk",
    ) -> str | None:
        """Send a TC card notification via DingTalk createAndDeliver.

        The card shell renders ``reason`` (Markdown) via DDRichTextView,
        and the ``detailLink`` opens a teamclaw preview iframe with
        ``LLMComponent_v2.jsx`` for full detail + feedback form.

        Args:
            user_id: Recipient staffId.
            reason: Markdown content rendered in card shell
                (DDRichTextView).  ~2000 char limit.
            detail_link: DingTalk deep link (3-layer nested encoding)
                that opens the teamclaw preview page in sidebar.
            bot_id: Bot ID for the card ``outTrackId``.
            card_id: Aix card component ID (from config).
            notification_data: Full structured notification data,
                base64-encoded into the detailLink URL for the
                iframe React component to consume.
            out_track_id_prefix: Prefix for ``outTrackId`` field.
                Business callers should pass their own prefix
                (e.g. ``"gov-notify"`` for governance notifications).
                Defaults to ``"dingtalk"``.

        Returns:
            External message ID on success, ``None`` on failure.
        """
        ...
