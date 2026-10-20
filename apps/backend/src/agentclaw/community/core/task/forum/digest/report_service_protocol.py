"""Transport-agnostic service contract for BBS digest report batches."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, Sequence, runtime_checkable

from agentclaw.community.core.task.forum.digest.models import (
    ForumDigestReportBrief,
    ForumDigestReportCreateResult,
    ForumDigestReportDecisionResult,
    ForumDigestReportItem,
)


@runtime_checkable
class ForumDigestReportServiceProtocol(Protocol):
    @abstractmethod
    async def create_report(
        self,
        *,
        bot_id: str,
        client_request_id: str,
        brief: ForumDigestReportBrief,
        items: Sequence[ForumDigestReportItem],
    ) -> ForumDigestReportCreateResult:
        """Create one confirmation batch and deliver it to the Bot's owner.

        The report is idempotent by ``client_request_id``. A replay returns the
        original batch and does not create a second session or notification.
        """

    @abstractmethod
    async def report_decision(
        self,
        *,
        digest_id: str,
        bot_id: str,
        decision: str,
        decision_client_request_id: str,
    ) -> ForumDigestReportDecisionResult:
        """Report the owner's final decision for the entire digest batch."""


__all__ = ["ForumDigestReportServiceProtocol"]
