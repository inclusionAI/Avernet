"""Time conversion helpers for task-trajectory persistence.

The deployed ``gmt_*`` convention is an Asia/Shanghai wall-clock value stored
in timezone-less ORM ``DateTime`` attributes.  Keep all application-supplied
trajectory timestamps on that same convention so repository-generated head,
event, and backfill timestamps never differ
by the UTC+8 offset.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


def epoch_ms_to_storage_datetime(ms: int) -> datetime:
    """Convert epoch milliseconds to a timezone-less Beijing wall clock."""
    return datetime.fromtimestamp(ms / 1000.0, tz=BEIJING_TIMEZONE).replace(
        tzinfo=None
    )


def storage_now() -> datetime:
    """Return the current timezone-less Beijing wall clock for ``gmt_*``."""
    return datetime.now(BEIJING_TIMEZONE).replace(tzinfo=None)


def storage_datetime_to_epoch_ms(value: datetime | None) -> int:
    """Convert a persisted ``gmt_*`` value back to epoch milliseconds.

    Database drivers return ``TIMESTAMP`` values as naive datetimes.  Under the
    repository's deployed contract those values are Asia/Shanghai wall-clock
    values, so attach that zone before converting them to an instant.  Aware
    values already identify their instant and are converted directly.
    """
    if value is None:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=BEIJING_TIMEZONE)
    return int(value.timestamp() * 1000)
