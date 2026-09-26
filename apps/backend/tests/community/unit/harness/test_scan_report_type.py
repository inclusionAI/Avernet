"""Unit tests for scan_report_type derivation (spec: bot_health_0604/dim_report_scan_report_type_spec.md).

Covers:
  1. _get_scan_report_type helper — all branches
  2. DimReportItem construction — scan_report_type reflects scan_type
  3. DimHistoryRecordItem construction — scan_report_type reflects scan_type
"""
from __future__ import annotations

from agentclaw.community.adapters.http.harness.router import _get_scan_report_type
from agentclaw.community.adapters.http.harness.schemas import DimReportItem, DimHistoryRecordItem


# ── _get_scan_report_type helper tests ───────────────────────


class TestGetScanReportType:
    """Test _get_scan_report_type() covers all branches in the spec."""

    def test_none_returns_normal(self) -> None:
        assert _get_scan_report_type(None) == "normal"

    def test_empty_string_returns_normal(self) -> None:
        assert _get_scan_report_type("") == "normal"

    def test_full_returns_normal(self) -> None:
        assert _get_scan_report_type("full") == "normal"

    def test_verify_returns_normal(self) -> None:
        assert _get_scan_report_type("verify") == "normal"

    def test_offline_daily_returns_daily(self) -> None:
        assert _get_scan_report_type("offline_20260523") == "daily"

    def test_offline_pre_daily_returns_daily(self) -> None:
        assert _get_scan_report_type("offline_pre_20260523") == "daily"

    def test_offline_preduration_returns_daily(self) -> None:
        """preduration must NOT match — only independent "duration" segment counts."""
        assert _get_scan_report_type("offline_preduration_20260523_20260529") == "daily"

    def test_offline_pre_duration_returns_weekly(self) -> None:
        assert _get_scan_report_type("offline_pre_duration_20260523_20260529") == "weekly"

    def test_offline_duration_returns_weekly(self) -> None:
        assert _get_scan_report_type("offline_duration_20260523_20260529") == "weekly"

    def test_duration_only_returns_normal(self) -> None:
        assert _get_scan_report_type("duration_20260523_20260529") == "normal"

    def test_non_offline_duration_in_middle_returns_normal(self) -> None:
        assert _get_scan_report_type("a_duration_b") == "normal"


# ── DimReportItem construction tests ─────────────────────────


class TestDimReportItemScanReportType:
    """Verify DimReportItem.scan_report_type is correctly set from _get_scan_report_type()."""

    def test_default_is_normal(self) -> None:
        item = DimReportItem()
        assert item.scan_report_type == "normal"

    def test_explicit_normal(self) -> None:
        item = DimReportItem(scan_report_type="normal")
        assert item.scan_report_type == "normal"

    def test_explicit_daily(self) -> None:
        item = DimReportItem(scan_report_type="daily")
        assert item.scan_report_type == "daily"

    def test_explicit_weekly(self) -> None:
        item = DimReportItem(scan_report_type="weekly")
        assert item.scan_report_type == "weekly"

    def test_with_scan_type_full(self) -> None:
        item = DimReportItem(scan_type="full")
        # Default is "normal" unless caller passes scan_report_type explicitly
        assert item.scan_report_type == "normal"

    def test_with_scan_type_weekly(self) -> None:
        item = DimReportItem(
            scan_type="offline_pre_duration_20260523_20260529",
            scan_report_type=_get_scan_report_type("offline_pre_duration_20260523_20260529"),
        )
        assert item.scan_report_type == "weekly"


# ── DimHistoryRecordItem construction tests ──────────────────


class TestDimHistoryRecordItemScanReportType:
    """Verify DimHistoryRecordItem.scan_report_type is correctly set from _get_scan_report_type()."""

    def test_default_is_normal(self) -> None:
        item = DimHistoryRecordItem(id=1, bot_id="b", entity_id="e")
        assert item.scan_report_type == "normal"

    def test_explicit_normal(self) -> None:
        item = DimHistoryRecordItem(
            id=1, bot_id="b", entity_id="e", scan_report_type="normal",
        )
        assert item.scan_report_type == "normal"

    def test_explicit_daily(self) -> None:
        item = DimHistoryRecordItem(
            id=1, bot_id="b", entity_id="e", scan_report_type="daily",
        )
        assert item.scan_report_type == "daily"

    def test_explicit_weekly(self) -> None:
        item = DimHistoryRecordItem(
            id=1, bot_id="b", entity_id="e", scan_report_type="weekly",
        )
        assert item.scan_report_type == "weekly"

    def test_with_scan_type_full(self) -> None:
        item = DimHistoryRecordItem(id=1, bot_id="b", entity_id="e", scan_type="full")
        assert item.scan_report_type == "normal"

    def test_with_scan_type_weekly(self) -> None:
        item = DimHistoryRecordItem(
            id=1,
            bot_id="b",
            entity_id="e",
            scan_type="offline_duration_20260523_20260529",
            scan_report_type=_get_scan_report_type("offline_duration_20260523_20260529"),
        )
        assert item.scan_report_type == "weekly"

    def test_with_scan_type_preduration_is_daily(self) -> None:
        item = DimHistoryRecordItem(
            id=1,
            bot_id="b",
            entity_id="e",
            scan_type="offline_preduration_20260523_20260529",
            scan_report_type=_get_scan_report_type("offline_preduration_20260523_20260529"),
        )
        assert item.scan_report_type == "daily"
