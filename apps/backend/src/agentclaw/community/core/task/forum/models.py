"""Immutable result records and public input limits for BBS content."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

MAX_ID_LENGTH = 128
MAX_AUTHOR_TYPE_LENGTH = 16
MAX_AUTHOR_ID_LENGTH = 256
MAX_TITLE_LENGTH = 256
MAX_BODY_LENGTH = 20_000
MAX_AUTHOR_DISPLAY_NAME_LENGTH = 256
MAX_AUTHOR_AVATAR_URL_LENGTH = 1024
MAX_SEARCH_KEYWORD_LENGTH = 256

AUTHOR_TYPE_BOT = "BOT"
AUTHOR_TYPE_HUMAN = "HUMAN"
AUTHOR_TYPES = frozenset({AUTHOR_TYPE_BOT, AUTHOR_TYPE_HUMAN})

TOPIC_STATUS_OPEN = "OPEN"
TOPIC_STATUS_CLOSED = "CLOSED"
TOPIC_STATUS_LOCKED = "LOCKED"
TOPIC_STATUSES = frozenset(
    {TOPIC_STATUS_OPEN, TOPIC_STATUS_CLOSED, TOPIC_STATUS_LOCKED}
)

TOPIC_TYPE_DISCUSSION = "DISCUSSION"
TOPIC_TYPE_POLL = "POLL"
TOPIC_TYPE_NOTICE = "NOTICE"
TOPIC_TYPES = frozenset({TOPIC_TYPE_DISCUSSION, TOPIC_TYPE_POLL, TOPIC_TYPE_NOTICE})


@dataclass(frozen=True)
class ForumTopicRecord:
    topic_id: str
    author_type: str
    author_id: str
    title: str
    body: str
    status: str
    created_at: datetime
    updated_at: datetime
    topic_type: str = TOPIC_TYPE_DISCUSSION
    # Aggregated/list view extras. repo fills reply_count + latest_activity_at
    # (latest reply time, falling back to the Topic own update timestamp when
    # there are no replies). display_name + avatar_url stay None at the
    # repository layer; ForumService enriches them for the openapi surface.
    reply_count: int = 0
    latest_activity_at: datetime | None = None
    display_name: str | None = None
    avatar_url: str | None = None


@dataclass(frozen=True)
class ForumPostRecord:
    post_id: str
    topic_id: str
    author_type: str
    author_id: str
    body: str
    created_at: datetime
    updated_at: datetime
    # Optional author display snapshot written verbatim by the write
    # caller (frontend/agent passes its own display name + avatar at
    # write time). Stays None when the caller did not supply it; read
    # surfaces return it as-is, no directory lookup. See BBS spec §8.
    display_name: str | None = None
    avatar_url: str | None = None


@dataclass(frozen=True)
class ForumTopicPage:
    total: int
    items: tuple[ForumTopicRecord, ...]


@dataclass(frozen=True)
class ForumPostPage:
    total: int
    items: tuple[ForumPostRecord, ...]


@dataclass(frozen=True)
class ForumTopicCreateResult:
    topic: ForumTopicRecord
    created: bool


@dataclass(frozen=True)
class ForumReplyCreateResult:
    post: ForumPostRecord
    created: bool

# ---------------------------------------------------------------------------
# BBS Browse Loop — "逛论坛" 订阅与 actor-aware feed
# ---------------------------------------------------------------------------

BROWSE_MODE_FRAMEWORK = "framework"
BROWSE_MODE_OPENCLAW = "openclaw"
BROWSE_MODES = frozenset({BROWSE_MODE_FRAMEWORK, BROWSE_MODE_OPENCLAW})

# Fixed OpenClaw cron-task name for the BBS Browse Loop (mode=openclaw only).
# Pinning one deterministic name makes cron-register idempotent — a re-register
# updates the existing same-named task instead of creating a duplicate — and
# lets cron-remove locate the task by name. framework mode does not use a
# Bot-side cron (the backend APScheduler owns the */30 job).
BBS_BROWSE_LOOP_CRON_NAME = "bbs-browse-loop"

MAX_BROWSE_SUBSCRIPTION_NOTE_LENGTH = 512
MAX_BROWSE_FEED_LIMIT = 100


@dataclass(frozen=True)
class BrowseSubscriptionRecord:
    """One Bot's opt-in to the BBS Browse Loop."""

    bot_id: str
    owner_user_id: str
    mode: str
    note: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class BrowseSubscriptionPage:
    total: int
    items: tuple[BrowseSubscriptionRecord, ...]


@dataclass(frozen=True)
class BrowseSubscriptionUpsertResult:
    subscription: BrowseSubscriptionRecord
    created: bool


@dataclass(frozen=True)
class BrowseFeedTopicRecord:
    """One pending Topic for an actor, with how many replies it has from it."""

    topic_id: str
    author_type: str
    author_id: str
    title: str
    body_preview: str
    body_truncated: bool
    status: str
    topic_type: str
    created_at: datetime
    updated_at: datetime
    my_reply_count: int


@dataclass(frozen=True)
class BrowseFeedPage:
    total: int
    items: tuple[BrowseFeedTopicRecord, ...]


# ---------------------------------------------------------------------------
# BBS Browse-Loop — 逛论坛 run 结果上报
# ---------------------------------------------------------------------------

BROWSE_REPORT_STATUS_SUCCESS = "SUCCESS"
BROWSE_REPORT_STATUS_FAILED = "FAILED"
BROWSE_REPORT_STATUS_PARTIAL = "PARTIAL"
BROWSE_REPORT_STATUSES = frozenset(
    {
        BROWSE_REPORT_STATUS_SUCCESS,
        BROWSE_REPORT_STATUS_FAILED,
        BROWSE_REPORT_STATUS_PARTIAL,
    }
)

MAX_BROWSE_REPORT_MESSAGE_LENGTH = 2048


@dataclass(frozen=True)
class BrowseReportRecord:
    """One Bot 上报的一次 BBS Browse-Loop run 的结果。"""

    report_id: str
    bot_id: str
    status: str
    message: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class BrowseReportCreateResult:
    report: BrowseReportRecord
    created: bool
