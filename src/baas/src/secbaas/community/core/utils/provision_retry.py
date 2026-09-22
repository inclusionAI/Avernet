"""Attempt deadline and backoff arithmetic for the publish retry pipeline.

Pure functions with no repository, service, or PaaS dependencies, so the
retry timing rules can be unit tested in isolation and reused by both the
inline failure paths and the periodic sweep.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from secbaas.community.core.utils.time_utils import naive_cst_now

RETRY_BACKOFF_BASE_SECONDS = 0.5
MAX_RETRY_BACKOFF_SECONDS = 30.0
ATTEMPT_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def attempt_started_at() -> str:
    return naive_cst_now().strftime(ATTEMPT_TIMESTAMP_FORMAT)


def parse_attempt_started_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, ATTEMPT_TIMESTAMP_FORMAT)
    except (ValueError, TypeError):
        return None


def is_attempt_expired(
    started_at: str | None, timeout_seconds: int, now: datetime | None = None
) -> bool:
    """Whether a claimed attempt has exceeded its time limit.

    An unreadable or absent timestamp is treated as expired, so a record
    carrying corrupt retry state is recovered rather than stranded.
    """
    parsed = parse_attempt_started_at(started_at)
    if parsed is None:
        return True
    reference = now or naive_cst_now()
    return reference >= parsed + timedelta(seconds=timeout_seconds)


def retry_backoff_seconds(attempt_ordinal: int) -> float:
    """Exponential backoff before the attempt numbered ``attempt_ordinal``.

    ``attempt_ordinal`` is 1-based for the first retry, so the first wait is
    one base interval and each later wait doubles, capped at
    ``MAX_RETRY_BACKOFF_SECONDS``.
    """
    if attempt_ordinal < 1:
        return 0.0
    delay = RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt_ordinal - 1))
    return min(delay, MAX_RETRY_BACKOFF_SECONDS)


def has_retry_budget(retry_count: int, retry_times: int) -> bool:
    return retry_count < retry_times


def publish_deadline(created_at: datetime, max_duration_seconds: int) -> datetime:
    return created_at + timedelta(seconds=max_duration_seconds)


def is_publish_deadline_exceeded(
    created_at: datetime | None,
    max_duration_seconds: int,
    now: datetime | None = None,
) -> bool:
    if not isinstance(created_at, datetime):
        return False
    reference = now or naive_cst_now()
    return reference >= publish_deadline(created_at, max_duration_seconds)
