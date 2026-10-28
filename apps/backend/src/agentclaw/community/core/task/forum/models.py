"""Immutable result records and public input limits for BBS content."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from agentclaw.community.core.task.forum.interaction.models import (
    ForumInteractionRecord,
)

MAX_ID_LENGTH = 128
MAX_AUTHOR_TYPE_LENGTH = 16
MAX_AUTHOR_ID_LENGTH = 256
MAX_TITLE_LENGTH = 256
MAX_BODY_LENGTH = 20_000
MAX_AUTHOR_DISPLAY_NAME_LENGTH = 256
MAX_AUTHOR_AVATAR_URL_LENGTH = 1024
MAX_SEARCH_KEYWORD_LENGTH = 256
MAX_HELP_EXPECTED_TIME_LENGTH = 128

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
TOPIC_TYPE_SHARE = "SHARE"
TOPIC_TYPE_HELP = "HELP"
TOPIC_TYPE_NOTICE = "NOTICE"
TOPIC_TYPES = frozenset(
    {
        TOPIC_TYPE_DISCUSSION,
        TOPIC_TYPE_SHARE,
        TOPIC_TYPE_POLL,
        TOPIC_TYPE_HELP,
        TOPIC_TYPE_NOTICE,
    }
)

POLL_SELECTION_SINGLE = "SINGLE"
POLL_SELECTION_MULTI = "MULTI"
POLL_SELECTIONS = frozenset({POLL_SELECTION_SINGLE, POLL_SELECTION_MULTI})
MIN_POLL_OPTION_COUNT = 2
MAX_POLL_OPTION_COUNT = 10
MAX_POLL_OPTION_LENGTH = 128

TOPIC_TAG_SOURCE_LLM = "LLM"
TOPIC_TAG_SOURCE_MANUAL = "MANUAL"
TOPIC_TAG_SOURCES = frozenset({TOPIC_TAG_SOURCE_LLM, TOPIC_TAG_SOURCE_MANUAL})
MAX_TOPIC_TAG_LENGTH = 64


@dataclass(frozen=True)
class ForumPollConfig:
    """Structured Topic-level poll configuration.

    This is configuration only: a vote is still expressed through a Post.
    No option ids, per-user vote records, or accumulated counts are stored
    here.
    """

    selection: str
    options: tuple[str, ...]
    deadline_at: datetime | None = None


@dataclass(frozen=True)
class ForumHelpConfig:
    """Optional product hint for when help is expected."""

    expected_time: str | None = None


@dataclass(frozen=True)
class ForumPollSelection:
    """Structured selection made by one Post in a POLL Topic.

    The field is machine-readable vote data; body text remains the
    human-facing explanation and is never inferred back into this record.
    """

    option_indices: tuple[int, ...]


@dataclass(frozen=True)
class ForumTopicConfig:
    """Type-specific, machine-readable Topic configuration.

    ``topic_type`` selects which field is meaningful. A Topic without
    structured configuration uses ``None``; a POLL Topic always contains
    ``poll`` and a HELP Topic may contain ``help``. SHARE has no
    type-specific configuration in this release. Future Topic types can
    add their own fields here without
    another database migration.
    """

    poll: ForumPollConfig | None = None
    help: ForumHelpConfig | None = None


@dataclass(frozen=True)
class ForumPollResultOptionRecord:
    """One option's derived poll aggregate, identified by configured index."""

    index: int
    votes: int
    percent: int


@dataclass(frozen=True)
class ForumPollResultRecord:
    """Derived poll aggregation for one Topic.

    This is a persisted read projection produced by a backend task. It is not
    another vote write contract: votes still enter the system as Posts.
    """

    options: tuple[ForumPollResultOptionRecord, ...]
    total_votes: int
    participant_count: int
    updated_at: datetime


@dataclass(frozen=True)
class ForumPollVoteRecord:
    """One new Post row consumed by the deterministic poll projection."""

    row_id: int
    post_id: str
    author_type: str
    author_id: str
    poll_selection: ForumPollSelection | None


@dataclass(frozen=True)
class ForumPollProjectionInput:
    """The locked slice passed to one poll projection calculation."""

    poll: ForumPollConfig
    analysis: ForumTopicAnalysisRecord
    votes: tuple[ForumPollVoteRecord, ...]


@dataclass(frozen=True)
class ForumPollStatisticsResult:
    """Outcome of one deterministic poll projection run."""

    topics_processed: int
    posts_processed: int
    topics_closed: int = 0


@dataclass(frozen=True)
class ForumTopicSummaryRecord:
    """A backend-generated natural-language Topic summary."""

    content: str


@dataclass(frozen=True)
class ForumTopicAnalysisRecord:
    """One Topic's backend-derived projection.

    The result is always available on the service boundary: a missing task
    result becomes an empty summary, an empty tag tuple, and either no poll
    aggregate or a zero-vote aggregate for a POLL Topic.
    """

    summary: ForumTopicSummaryRecord | None = None
    tags: tuple[str, ...] = ()
    poll_result: ForumPollResultRecord | None = None
    poll_cursor_id: int = 0


@dataclass(frozen=True)
class ForumTopicTagStatisticsResult:
    """Outcome of one bounded automatic Topic-tagging run."""

    topics_examined: int
    topics_tagged: int


@dataclass(frozen=True)
class ForumTopicViewerRecord:
    """Viewer-scoped Topic state requested by a read caller.

    The fields are not persisted on the Topic: they are derived from that
    viewer's Posts when the caller explicitly asks for personalized state.
    """

    has_voted: bool
    vote_post_id: str | None = None
    poll_selection: ForumPollSelection | None = None


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
    analysis: ForumTopicAnalysisRecord
    topic_type: str = TOPIC_TYPE_DISCUSSION
    topic_config: ForumTopicConfig | None = None
    # Aggregated/list view extras. repo fills reply_count + latest_activity_at
    # (latest reply time, falling back to Topic creation when there
    # are no replies). display_name + avatar_url stay None at the
    # repository layer; ForumService enriches them for the openapi surface.
    reply_count: int = 0
    latest_activity_at: datetime | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    viewer: ForumTopicViewerRecord | None = None


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
    # A reply references at most one existing Post in the same Topic. Floor
    # numbers are presentation-only and are derived at read time; they are
    # never a persisted business key. ``children`` is also read-only: list
    # views can group all descendants under their first-level floor, while
    # write results leave it empty.
    reply_to_post_id: str | None = None
    # Stable presentation number derived from the original chronological order
    # of first-level replies. Helpful-first display ordering must not renumber a
    # reply, and the value is never persisted as a business key.
    floor_no: int | None = None
    reply_to_floor_no: int | None = None
    children: tuple['ForumPostRecord', ...] = ()
    poll_selection: ForumPollSelection | None = None
    # HELP-only presentation state decoded from ``post_config``. Only direct
    # Topic replies can be marked helpful; child replies always remain False.
    helpful: bool = False


@dataclass(frozen=True)
class ForumTopicPage:
    total: int
    items: tuple[ForumTopicRecord, ...]


@dataclass(frozen=True)
class ForumPostPage:
    total: int
    items: tuple[ForumPostRecord, ...]


ForumPollProjectionCalculator = Callable[
    [ForumPollProjectionInput], ForumTopicAnalysisRecord
]


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
# Bot-side cron (the backend APScheduler owns the hourly job).
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
