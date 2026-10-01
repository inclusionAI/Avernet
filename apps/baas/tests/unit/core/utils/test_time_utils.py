"""Unit tests for the renewal-pipeline clock helpers (CR-01).

Pins the fixed Asia/Shanghai (+08:00, no DST) clock domain the renewal
pipeline converges on: every helper's output is host-timezone
independent, so these expectations hold under any host TZ (the suite is
additionally run with TZ=America/New_York in verification).
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from secbaas.community.core.utils.time_utils import (
    CST,
    format_ttl_expiration_time,
    naive_cst_fromtimestamp,
    naive_cst_now,
    renewal_window,
)

# Ground truth (plan-pinned): 1750000000000 ms = 2025-06-15 23:06:40 +08:00.
_TTL_MS = 1750000000000


def test_cst_constant_is_fixed_asia_shanghai():
    assert isinstance(CST, ZoneInfo)
    assert str(CST) == "Asia/Shanghai"
    assert CST.utcoffset(datetime(2025, 6, 15, 12, 0, 0)) == timedelta(hours=8)


def test_naive_cst_fromtimestamp_pins_fixed_zone_wall_clock():
    dt = naive_cst_fromtimestamp(_TTL_MS / 1000)
    assert dt == datetime(2025, 6, 15, 23, 6, 40)
    assert dt.tzinfo is None


def test_naive_cst_now_matches_fixed_zone_within_tolerance():
    now = naive_cst_now()
    assert now.tzinfo is None
    expected = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    assert abs((now - expected).total_seconds()) <= 5


def test_format_ttl_expiration_time_renders_fixed_zone_string():
    assert format_ttl_expiration_time(_TTL_MS) == "2025-06-15 23:06:40"


@pytest.mark.parametrize(
    ("ttl_minutes", "expected"),
    [
        (1440, timedelta(hours=12)),
        (2880, timedelta(hours=24)),
        (90, timedelta(minutes=45)),
    ],
)
def test_renewal_window_is_half_the_ttl_period(ttl_minutes, expected):
    assert renewal_window(ttl_minutes) == expected


def test_renewal_window_floors_odd_periods_and_coerces_strings():
    # int//2 semantics: odd periods floor, and the int() coercion handles
    # quoted YAML values (WR-03).
    assert renewal_window(91) == timedelta(minutes=45)
    assert renewal_window("1440") == timedelta(hours=12)
