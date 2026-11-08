"""Repository contract for BBS digest report batches."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.forum.digest.models import (
        ForumDigestReportBrief,
        ForumDigestReportCreateResult,
        ForumDigestReportDecisionResult,
        ForumDigestReportItem,
        ForumDigestReportRecord,
    )


@runtime_checkable
class ForumDigestReportRepositoryProtocol(Protocol):
    @abstractmethod
    def create_report(
        self,
        *,
        bot_id: str,
        owner_user_id: str,
        client_request_id: str,
        brief: ForumDigestReportBrief,
        items: tuple[ForumDigestReportItem, ...],
    ) -> ForumDigestReportCreateResult:
        """Create one digest batch, or replay its idempotency key unchanged."""

    @abstractmethod
    def get_report(self, digest_id: str) -> ForumDigestReportRecord | None:
        """Return one digest batch in the current tenant and environment."""

    @abstractmethod
    def get_report_by_request(
        self, bot_id: str, client_request_id: str
    ) -> ForumDigestReportRecord | None:
        """Return one Bot's idempotency-keyed digest batch, if it exists."""

    @abstractmethod
    def update_delivery(
        self,
        *,
        digest_id: str,
        session_id: str,
        session_url: str,
        notification_status: str,
    ) -> ForumDigestReportRecord | None:
        """Persist a created confirmation session and notification outcome."""

    @abstractmethod
    def mark_failed(self, digest_id: str) -> ForumDigestReportRecord | None:
        """Mark a still-pending digest batch as failed during delivery."""

    @abstractmethod
    def apply_decision(
        self,
        *,
        digest_id: str,
        decision: str,
        decision_client_request_id: str,
    ) -> ForumDigestReportDecisionResult:
        """Apply one final batch decision; a repeated decision is idempotent."""


__all__ = ["ForumDigestReportRepositoryProtocol"]
