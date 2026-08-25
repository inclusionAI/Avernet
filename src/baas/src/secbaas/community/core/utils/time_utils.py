"""Fixed Asia/Shanghai clock helpers for the ARCA TTL renewal pipeline.

Clock-domain convention (CR-01): every DB-facing timestamp the renewal
pipeline writes — ``next_renew_at`` in register/update/postpone paths and
their comparisons — MUST be a naive *Asia/Shanghai (+08:00, no DST)* wall
clock. The original defect was mixing multiple clock domains (naive UTC
vs host-local time), which desynced the due gate; the pipeline therefore
converges on ONE fixed zone so that every write and every comparison
share a single clock. The scheduler passes an explicit naive-Asia/Shanghai
``now`` bound parameter to ``list_due_for_renewal`` so the comparison
stays time-zone independent of the DB server clock as well (company
MySQL runs its session on +08:00, matching the stored wall clock).

Do NOT call unqualified ``datetime.now()`` / ``datetime.fromtimestamp()``
for pipeline timestamps: on hosts outside the fixed zone they produce
the host-local wall clock and desync the due gate by the time-zone
offset. ``naive_cst_now`` / ``naive_cst_fromtimestamp`` are the ONLY
clock entry points for pipeline timestamps.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Fixed pipeline clock domain (CR-01): Asia/Shanghai is +08:00 with no DST,
# so every wall-clock value derived from this zone is host-timezone
# independent.
CST = ZoneInfo("Asia/Shanghai")


def naive_cst_now() -> datetime:
    """Current time as a naive Asia/Shanghai (+08:00) datetime.

    Safe to compare/persist — always renders the fixed +08:00 wall clock,
    never the host-local one.
    """
    return datetime.now(CST).replace(tzinfo=None)


def naive_cst_fromtimestamp(ts: float) -> datetime:
    """Epoch seconds (or millis) → naive Asia/Shanghai (+08:00) wall clock.

    ``datetime.fromtimestamp`` interprets the epoch in the fixed zone, so
    the result is host-timezone independent (Asia/Shanghai has no DST).
    """
    return datetime.fromtimestamp(ts, tz=CST).replace(tzinfo=None)


def format_ttl_expiration_time(ts_ms: float) -> str:
    """Epoch milliseconds → '%Y-%m-%d %H:%M:%S' in fixed Asia/Shanghai.

    Host-timezone independent (always renders the +08:00 wall clock), and
    byte-identical to the values the health_check scanners write
    (_sandbox_device_router.py / _service_device_provider.py).
    """
    return naive_cst_fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


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