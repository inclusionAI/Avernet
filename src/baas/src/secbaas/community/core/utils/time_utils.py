"""Naive-UTC clock helpers for the ARCA TTL renewal pipeline.

Clock-domain convention (CR-01): every DB-facing timestamp the renewal
pipeline writes — ``next_renew_at`` in register/update/postpone paths and
their comparisons — MUST be a *naive UTC* wall clock. The due gate
depends on it: SQLite's ``CURRENT_TIMESTAMP`` is always UTC, and the
scheduler passes an explicit naive-UTC ``now`` bound parameter to
``list_due_for_renewal`` so the comparison stays time-zone independent
for MySQL (whose ``NOW()`` follows the server time zone) as well.

Do NOT call unqualified ``datetime.now()`` / ``datetime.fromtimestamp()``
for pipeline timestamps: on non-UTC hosts they produce the local wall
clock and desync the due gate by the time-zone offset (empirically
reproduced on a CST host, where a row "due 1 minute ago" written in
local time was judged NOT due by SQLite's UTC clock).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def naive_utc_now() -> datetime:
    """Current time as a naive UTC datetime (safe to compare/persist)."""
    return datetime.now(UTC).replace(tzinfo=None)


def naive_utc_fromtimestamp(ts: float) -> datetime:
    """Epoch seconds (or millis) → naive UTC wall-clock datetime."""
    return datetime.fromtimestamp(ts, tz=UTC).replace(tzinfo=None)


def renewal_window(default_ttl_minutes: int) -> timedelta:
    """Renewal lead window before TTL expiry — half the configured TTL period.

    Single source of truth for the lead window shared by
    ``DeadlineRenewalScheduler._renewal_window`` (discovery scan, postpone
    and success paths) and the lifecycle wrapper's register target, so a
    reconfigured ``arca.default_ttl_minutes`` keeps every writer coherent
    (WR-02). The default 1440 minutes resolves to 12h — byte-identical to
    the former hardcoded ``timedelta(hours=12)``.
    """
    return timedelta(minutes=int(default_ttl_minutes) // 2)
