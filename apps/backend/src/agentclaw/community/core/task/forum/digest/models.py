"""Transport-agnostic records for BBS high-value digest batches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

MAX_DIGEST_REPORT_ITEM_COUNT = 50
MAX_DIGEST_ITEM_SOURCE_LENGTH = 32
MAX_DIGEST_BRIEF_SUMMARY_LENGTH = 2_000
MAX_DIGEST_BRIEF_HIGHLIGHT_COUNT = 20
MAX_DIGEST_BRIEF_HIGHLIGHT_LENGTH = 256
MAX_DIGEST_ITEM_TITLE_LENGTH = 256
MAX_DIGEST_ITEM_TEXT_LENGTH = 20_000
MAX_DIGEST_ITEM_REASON_LENGTH = 2_000
MAX_DIGEST_ITEM_TAG_LENGTH = 64
MAX_DIGEST_RETENTION_TARGET_LENGTH = 128
MAX_DIGEST_SESSION_URL_LENGTH = 2_048

DIGEST_REPORT_STATUS_PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
DIGEST_REPORT_STATUS_CONFIRMED = "CONFIRMED"
DIGEST_REPORT_STATUS_REJECTED = "REJECTED"
DIGEST_REPORT_STATUS_FAILED = "FAILED"
DIGEST_REPORT_STATUSES = frozenset(
    {
        DIGEST_REPORT_STATUS_PENDING_CONFIRMATION,
        DIGEST_REPORT_STATUS_CONFIRMED,
        DIGEST_REPORT_STATUS_REJECTED,
        DIGEST_REPORT_STATUS_FAILED,
    }
)
DIGEST_REPORT_FINAL_DECISIONS = frozenset(
    {DIGEST_REPORT_STATUS_CONFIRMED, DIGEST_REPORT_STATUS_REJECTED}
)

DIGEST_NOTIFICATION_STATUS_PENDING = "PENDING"
DIGEST_NOTIFICATION_STATUS_SUBMITTED = "SUBMITTED"
DIGEST_NOTIFICATION_STATUS_FAILED = "FAILED"
DIGEST_NOTIFICATION_STATUSES = frozenset(
    {
        DIGEST_NOTIFICATION_STATUS_PENDING,
        DIGEST_NOTIFICATION_STATUS_SUBMITTED,
        DIGEST_NOTIFICATION_STATUS_FAILED,
    }
)


@dataclass(frozen=True)
class ForumDigestReportBrief:
    """The Bot-produced, whole-batch summary shown in the owner notification."""

    summary: str
    highlights: tuple[str, ...]


@dataclass(frozen=True)
class ForumDigestReportItem:
    """One candidate that the Bot will render inside the confirmation session."""

    source: str
    topic_id: str
    title: str
    content: str
    reason: str
    tags: tuple[str, ...]
    retention_target: str
    post_id: str | None = None


@dataclass(frozen=True)
class ForumDigestReportRecord:
    """One batch-level digest report and its delivery metadata."""

    digest_id: str
    bot_id: str
    owner_user_id: str
    client_request_id: str
    brief: ForumDigestReportBrief
    items: tuple[ForumDigestReportItem, ...]
    status: str
    notification_status: str
    created_at: datetime
    updated_at: datetime
    session_id: str | None = None
    session_url: str | None = None
    decision_client_request_id: str | None = None
    decided_at: datetime | None = None


@dataclass(frozen=True)
class ForumDigestReportCreateResult:
    digest: ForumDigestReportRecord
    created: bool


@dataclass(frozen=True)
class ForumDigestReportDecisionResult:
    digest: ForumDigestReportRecord
    changed: bool


__all__ = [
    "DIGEST_NOTIFICATION_STATUSES",
    "DIGEST_NOTIFICATION_STATUS_FAILED",
    "DIGEST_NOTIFICATION_STATUS_PENDING",
    "DIGEST_NOTIFICATION_STATUS_SUBMITTED",
    "DIGEST_REPORT_FINAL_DECISIONS",
    "DIGEST_REPORT_STATUSES",
    "DIGEST_REPORT_STATUS_CONFIRMED",
    "DIGEST_REPORT_STATUS_FAILED",
    "DIGEST_REPORT_STATUS_PENDING_CONFIRMATION",
    "DIGEST_REPORT_STATUS_REJECTED",
    "ForumDigestReportCreateResult",
    "ForumDigestReportDecisionResult",
    "ForumDigestReportBrief",
    "ForumDigestReportItem",
    "ForumDigestReportRecord",
    "MAX_DIGEST_BRIEF_HIGHLIGHT_COUNT",
    "MAX_DIGEST_BRIEF_HIGHLIGHT_LENGTH",
    "MAX_DIGEST_BRIEF_SUMMARY_LENGTH",
    "MAX_DIGEST_ITEM_REASON_LENGTH",
    "MAX_DIGEST_ITEM_SOURCE_LENGTH",
    "MAX_DIGEST_ITEM_TAG_LENGTH",
    "MAX_DIGEST_ITEM_TEXT_LENGTH",
    "MAX_DIGEST_ITEM_TITLE_LENGTH",
    "MAX_DIGEST_REPORT_ITEM_COUNT",
    "MAX_DIGEST_RETENTION_TARGET_LENGTH",
    "MAX_DIGEST_SESSION_URL_LENGTH",
]
