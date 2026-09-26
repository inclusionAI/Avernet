"""Datetime parsing helpers for the bot-dormant scan.

Extracted from ``service.py`` to keep that module focused on the dormant
lifecycle (Rule 9 — single responsibility). These are generic ISO-8601
helpers with no dormant-specific state.
"""
from __future__ import annotations

from datetime import datetime

from agentclaw.community.log import get_logger


logger = get_logger()


def parse_dt(ts: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp string to a naive UTC datetime, or None."""
    if not ts:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            continue
    logger.warning("[dormant.datetime_utils] Cannot parse datetime: %s", ts)
    return None


def max_datetime(*dts: datetime | None) -> datetime | None:
    """Return the latest non-None datetime in *dts, or None if all are None."""
    valid = [d for d in dts if d is not None]
    return max(valid) if valid else None
