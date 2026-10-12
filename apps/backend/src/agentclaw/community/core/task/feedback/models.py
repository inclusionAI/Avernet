"""Immutable result records and public input limits for general feedback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Public input limits, mirroring the forum content limits. The ``module`` is an
# open string (``bbs`` / ``task`` / ...): new feedback sources add a value with
# no schema change, so it carries only a length cap.
MAX_REPORTER_ID_LENGTH = 256
MAX_MODULE_LENGTH = 64
MAX_CONTENT_LENGTH = 4_000


@dataclass(frozen=True)
class FeedbackRecord:
    id: int
    reporter_id: str
    module: str
    content: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class FeedbackPage:
    total: int
    items: tuple[FeedbackRecord, ...]


@dataclass(frozen=True)
class FeedbackCreateResult:
    feedback: FeedbackRecord
    created: bool
