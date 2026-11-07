"""Time helpers for BBS interaction persistence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


def storage_now() -> datetime:
    """Return the current timezone-less Beijing wall clock."""
    return datetime.now(BEIJING_TIMEZONE).replace(tzinfo=None)
