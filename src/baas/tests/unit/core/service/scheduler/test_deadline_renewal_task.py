"""Unit tests for DeadlineRenewalScheduler Steps 0-2 (deep mock per D-05).

Covers: run() reentrant guards + lock dispatch (incl. F4 env-scoped lock
name), Step 0 gap detection + discovery scan, Step 1 cold-table query +
orphan detection, Step 2 concurrent renewal scaffolding, Steps 3-5
(renewal decision / failure handling / report+metrics), CR-GAP-01
discovery-scan isolation and WR-GAP-01 per-record isolation.

Four-layer deep mock: repo, lock_service, paas_facade, metrics all mocked.
Migrated from enterprise tests/unit/core/arca_ttl_renewal/test_scheduler.py
with imports rewritten to secbaas.community.* and the repo mock spec'd to
the community TtlRenewalScheduleRepository Protocol.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.core.repository.arca_ttl import TtlRenewalScheduleRepository
from secbaas.community.core.service.scheduler import (
    DeadlineRenewalScheduler,
    DeadlineRenewalSchedulerConfig,
    GapDetectionResult,
    RenewalRunReport,
)
from secbaas.community.core.utils.env_utils import get_current_env


def _acquired_lock():
    """Mock lock context reporting acquired=True."""
    return SimpleNamespace(acquired=True)


def _not_acquired_lock():
    """Mock lock context reporting acquired=False."""
    return SimpleNamespace(acquired=False)


def _lock_service_acquired():
    """Mock lock_service.try_lock that yields an acquired lock context."""
    svc = MagicMock()
    svc.try_lock.return_value.__enter__.return_value = _acquired_lock()
    return svc


def _lock_service_not_acquired():
    """Mock lock_service.try_lock that yields a non-acquired lock context."""
    svc = MagicMock()
    svc.try_lock.return_value.__enter__.return_value = _not_acquired_lock()
    return svc


def _make_scheduler(
    *,
    enabled: bool = True,
    config_overrides: dict | None = None,
    lock_acquired: bool = True,
) -> tuple[DeadlineRenewalScheduler, MagicMock, MagicMock, MagicMock]:
    """Build a DeadlineRenewalScheduler with fully mocked dependencies.

    Returns (scheduler, mock_repo, mock_lock_service, mock_paas_facade).
    """
    cfg_kwargs = {"enabled": enabled, "batch_size": 500, "max_concurrency": 20,
                  "anti_join_verify_interval_cycles": 48, "env": "test"}
    if config_overrides:
        cfg_kwargs.update(config_overrides)
    config = DeadlineRenewalSchedulerConfig(**cfg_kwargs)

    mock_repo = MagicMock(spec=TtlRenewalScheduleRepository)
    mock_paas_facade = MagicMock()

    if lock_acquired:
        mock_lock_service = _lock_service_acquired()
    else:
        mock_lock_service = _lock_service_not_acquired()

    scheduler = DeadlineRenewalScheduler(
        config=config,
        lock_service=mock_lock_service,
        schedule_repo=mock_repo,
        paas_facade=mock_paas_facade,
    )
    return scheduler, mock_repo, mock_lock_service, mock_paas_facade


class TestRunGuards:
    """Tests for run() early-return guards (disabled, lock, reentrant)."""

    @pytest.mark.asyncio
    async def test_disabled_config_skips_lock_and_returns_none(self):
        """Test 1: config.enabled=False → return None, lock NOT called."""
        scheduler, mock_repo, mock_lock, _ = _make_scheduler(enabled=False)
        result = await scheduler.run()
        assert result is None
        mock_lock.try_lock.assert_not_called()
        mock_repo.count_active.assert_not_called()

    @pytest.mark.asyncio
    async def test_lock_not_acquired_returns_none(self):
        """Test 2: lock.acquired=False → return None, repo methods NOT called."""
        scheduler, mock_repo, _, _ = _make_scheduler(
            enabled=True, lock_acquired=False,
        )
        result = await scheduler.run()
        assert result is None
        mock_repo.count_active.assert_not_called()
        mock_repo.list_due_for_renewal.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_running_returns_none(self):
        """Test 3: _running=True → return None (reentrant guard)."""
        scheduler, mock_repo, mock_lock, _ = _make_scheduler(enabled=True)
        scheduler._running = True  # simulate concurrent run
        result = await scheduler.run()
        assert result is None
        # Lock is never attempted because short-circuit happens first
        mock_repo.count_active.assert_not_called()

    @pytest.mark.asyncio
    async def test_lock_acquired_dispatches_to_run_once(self):
        """Test 4: lock acquired → dispatches to _run_once, clears _running."""
        scheduler, _, _, _ = _make_scheduler(enabled=True, lock_acquired=True)
        mock_report = RenewalRunReport(run_uuid="test-uuid", trigger="cron")
        scheduler._run_once = AsyncMock(return_value=mock_report)

        result = await scheduler.run()
        scheduler._run_once.assert_awaited_once()
        assert result is mock_report
        assert scheduler._running is False

    @pytest.mark.asyncio
    async def test_run_uses_env_scoped_lock_name(self):
        """F4/D-11': try_lock receives the env-scoped resolved lock name.

        pre/prod share one MySQL instance (and lock table); a fixed lock
        name would make the two environments' schedulers take turns.
        """
        scheduler, _, mock_lock, _ = _make_scheduler(enabled=True)
        mock_report = RenewalRunReport(run_uuid="test-uuid", trigger="cron")
        scheduler._run_once = AsyncMock(return_value=mock_report)

        await scheduler.run()

        kwargs = mock_lock.try_lock.call_args.kwargs
        assert kwargs["lock_name"] == scheduler._config.resolved_lock_name()
        assert kwargs["lock_name"] == f"{scheduler._config.lock_name}_{get_current_env()}"
        # The bare config lock name must never be used directly.
        assert kwargs["lock_name"] != scheduler._config.lock_name
        assert kwargs["expire_seconds"] == scheduler._config.lock_expire_seconds
        assert kwargs["block"] is False


class TestStep0GapDetection:
    """Tests for Step 0 — gap detection + discovery scan."""

    @pytest.mark.asyncio
    async def test_gap_cold_equals_hot_no_discovery_scan(self):
        """Test 5: cold == hot → gap=0, discovery scan NOT triggered."""
        scheduler, mock_repo, _, _ = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 50000
        # Mock hot counts: count_hot_arca_devices + count_hot_arca_bindings
        mock_repo.count_hot_arca_devices.return_value = 40000
        mock_repo.count_hot_arca_bindings.return_value = 10000
        mock_repo.list_due_for_renewal.return_value = []

        scheduler._round_count = 0
        report = await scheduler._run_once()

        mock_repo.count_active.assert_called_once()
        mock_repo.find_unregistered.assert_not_called()
        assert isinstance(report, RenewalRunReport)
        assert report.gap_detected is False

    @pytest.mark.asyncio
    async def test_gap_hot_greater_than_cold_triggers_discovery_scan(self):
        """Test 6: hot > cold → gap>0, discovery scan IS triggered."""
        scheduler, mock_repo, _, _ = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 45000  # cold
        mock_repo.count_hot_arca_devices.return_value = 40000
        mock_repo.count_hot_arca_bindings.return_value = 10000  # hot total = 50000
        mock_repo.list_due_for_renewal.return_value = []
        # find_unregistered returns empty to stop the loop after first call
        mock_repo.find_unregistered.return_value = []

        scheduler._round_count = 0
        report = await scheduler._run_once()

        # find_unregistered should be called for both sides (2 calls, both empty)
        assert mock_repo.find_unregistered.call_count >= 1
        assert report.gap_detected is True

    @pytest.mark.asyncio
    async def test_discovery_scan_loops_until_empty(self):
        """Test 7: find_unregistered() returns 500, 500, then empty → 3 calls."""
        scheduler, mock_repo, _, _ = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 0  # cold = 0
        mock_repo.count_hot_arca_devices.return_value = 500
        mock_repo.count_hot_arca_bindings.return_value = 500  # hot = 1000

        # Side effect: 1 batch for baas_device, then empty; empty for binding
        call_counts: dict[str, int] = {}

        def _find_unregistered_side_effect(env, side, limit=500):
            call_counts[side] = call_counts.get(side, 0) + 1
            if side == "baas_device" and call_counts[side] == 1:
                return [{"id": 1, "sandbox_id": "sb-1", "source_table": "baas_device", "ttl": "1760000000000"}]
            return []

        mock_repo.find_unregistered.side_effect = _find_unregistered_side_effect
        mock_repo.list_due_for_renewal.return_value = []
        mock_repo.register_if_missing = MagicMock()

        scheduler._round_count = 0
        report = await scheduler._run_once()

        # find_unregistered called for baas_device (2 calls: 1 with data, 1 empty) + ac_entity_device_binding (1 call, empty)
        assert mock_repo.find_unregistered.call_count >= 2
        assert mock_repo.register_if_missing.call_count >= 1
        assert report.gap_records_registered >= 1

    @pytest.mark.asyncio
    async def test_anti_join_periodic_verify_triggers_every_48_rounds(self):
        """Test 8: anti-join periodic verify fires on round 48 regardless of COUNT."""
        scheduler, mock_repo, _, _ = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 50000
        mock_repo.count_hot_arca_devices.return_value = 50000
        mock_repo.count_hot_arca_bindings.return_value = 0  # cold == hot
        mock_repo.list_due_for_renewal.return_value = []
        mock_repo.find_unregistered.return_value = []

        # Round 47: mod 48 = 47, periodic verify triggers? No: 47 % 48 = 47 ≠ 0
        # Round 48: mod 48 = 0, periodic verify triggers
        scheduler._round_count = 47  # about to become 48 in _run_once()
        report_48 = await scheduler._run_once()
        # At round 48, periodic verify should trigger
        assert mock_repo.find_unregistered.call_count >= 1
        # _round_count is now 48
        assert scheduler._round_count == 48

        # Round 49: mod 48 = 1, no periodic verify (and cold == hot)
        call_count_before = mock_repo.find_unregistered.call_count
        report_49 = await scheduler._run_once()
        assert mock_repo.find_unregistered.call_count == call_count_before  # no new calls

    @pytest.mark.asyncio
    async def test_hot_count_query_failure_does_not_crash(self):
        """Test 9: hot count query raises Exception → logs error, continues."""
        scheduler, mock_repo, _, _ = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 50000
        mock_repo.count_hot_arca_devices.side_effect = Exception("DB connection error")
        mock_repo.count_hot_arca_bindings.return_value = 0
        # Mock Step 1 to return empty
        mock_repo.list_due_for_renewal.return_value = []

        scheduler._round_count = 0
        report = await scheduler._run_once()

        # Should not crash; report is returned
        assert isinstance(report, RenewalRunReport)


class TestStep1ColdTableQuery:
    """Tests for Step 1 — cold table query + LEFT JOIN orphan detection."""

    def _due_row(self, source_table="baas_device", source_id=1,
                 sandbox_id="sb-1", hot_id=1):
        return {
            "id": source_id,
            "sandbox_id": sandbox_id,
            "source_table": source_table,
            "source_id": source_id,
            "next_renew_at": "2026-08-18 12:00:00",
            "renew_fail_count": 0,
            "device_props": "{}",
            "hot_id": hot_id,
        }

    @pytest.mark.asyncio
    async def test_cold_table_query_returns_results_and_sets_due_count(self):
        """Test 10: both source tables return results — due_count = sum."""
        scheduler, mock_repo, _, _ = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 200  # cold == hot, no gap
        mock_repo.count_hot_arca_devices.return_value = 100
        mock_repo.count_hot_arca_bindings.return_value = 100

        mock_repo.list_due_for_renewal.side_effect = [
            [self._due_row("baas_device", 1, "sb-1", hot_id=1) for _ in range(100)],
            [self._due_row("ac_entity_device_binding", 101, "sb-101", hot_id=101) for _ in range(100)],
        ]
        mock_repo.find_unregistered.return_value = []

        scheduler._round_count = 0
        report = await scheduler._run_once()

        assert mock_repo.list_due_for_renewal.call_count == 2
        assert report.due_count == 200

    @pytest.mark.asyncio
    async def test_cold_table_query_empty_returns_early(self):
        """Test 11: both list_due_for_renewal() return [] → early return."""
        scheduler, mock_repo, _, _ = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 200
        mock_repo.count_hot_arca_devices.return_value = 200
        mock_repo.count_hot_arca_bindings.return_value = 0
        mock_repo.list_due_for_renewal.return_value = []
        mock_repo.find_unregistered.return_value = []

        scheduler._round_count = 0
        report = await scheduler._run_once()

        assert report.due_count == 0
        # set_status should NOT be called (no orphans to process)
        mock_repo.set_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_orphan_detection_marks_stopped_and_excludes(self):
        """Test 12: hot_id=NULL → set_status(STOPPED), excluded from processing."""
        scheduler, mock_repo, _, _ = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 2
        mock_repo.count_hot_arca_devices.return_value = 1
        mock_repo.count_hot_arca_bindings.return_value = 1
        mock_repo.find_unregistered.return_value = []
        mock_repo.list_due_for_renewal.side_effect = [
            # baas_device: 1 valid + 1 orphan
            [
                self._due_row("baas_device", 1, "sb-1", hot_id=1),
                self._due_row("baas_device", 2, "sb-2", hot_id=None),
            ],
            # ac_entity_device_binding: empty
            [],
        ]

        scheduler._round_count = 0
        report = await scheduler._run_once()

        # set_status called for orphan
        mock_repo.set_status.assert_called_once_with("test", "baas_device", 2, "STOPPED")
        # du_count = 2 rows total
        assert report.due_count == 2

    @pytest.mark.asyncio
    async def test_orphan_set_status_exception_does_not_crash_scheduler(self):
        """Test 12b: orphan set_status raises → caught, scheduler continues."""
        scheduler, mock_repo, _, _ = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 2
        mock_repo.count_hot_arca_devices.return_value = 2
        mock_repo.count_hot_arca_bindings.return_value = 0
        mock_repo.find_unregistered.return_value = []
        mock_repo.set_status.side_effect = Exception("DB connection lost")
        mock_repo.list_due_for_renewal.side_effect = [
            [self._due_row("baas_device", 1, "sb-1", hot_id=None)],
            [],
        ]

        scheduler._round_count = 0
        report = await scheduler._run_once()

        # set_status was called but exception caught — scheduler survived
        mock_repo.set_status.assert_called_once()
        # No orphans counted (exception prevented increment)
        assert report.orphan_count == 0
        # Processing list empty (orphan excluded), so due_count=1 but no renewals
        assert report.due_count == 1
        assert isinstance(report, RenewalRunReport)


class TestStep2ConcurrentRenewalScaffolding:
    """Tests for Step 2 — asyncio.Semaphore + _renew_one placeholder."""

    def _due_row(self, source_table="baas_device", source_id=1,
                 sandbox_id="sb-1", hot_id=1):
        return {
            "id": source_id,
            "sandbox_id": sandbox_id,
            "source_table": source_table,
            "source_id": source_id,
            "next_renew_at": "2026-08-18 12:00:00",
            "renew_fail_count": 0,
            "device_props": "{}",
            "hot_id": hot_id,
        }

    @pytest.mark.asyncio
    async def test_step2_dispatches_concurrent_renewals(self):
        """Test 13: 3 due rows → Semaphore wraps 3 _renew_one calls."""
        scheduler, mock_repo, _, _ = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 3
        mock_repo.count_hot_arca_devices.return_value = 3
        mock_repo.count_hot_arca_bindings.return_value = 0
        mock_repo.find_unregistered.return_value = []

        rows = [self._due_row("baas_device", i, f"sb-{i}", hot_id=i) for i in range(1, 4)]
        mock_repo.list_due_for_renewal.side_effect = [rows, []]

        # Track _renew_one calls
        scheduler._renew_one = AsyncMock(return_value="skipped")

        scheduler._round_count = 0
        report = await scheduler._run_once()

        assert scheduler._renew_one.call_count == 3
        assert report.skipped == 3
        assert report.due_count == 3

    @pytest.mark.asyncio
    async def test_step1_query_failure_is_caught(self):
        """Test 14: list_due_for_renewal raises → caught, error logged."""
        scheduler, mock_repo, _, _ = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 100
        mock_repo.count_hot_arca_devices.return_value = 100
        mock_repo.count_hot_arca_bindings.return_value = 0
        mock_repo.find_unregistered.return_value = []
        mock_repo.list_due_for_renewal.side_effect = Exception("DB query error")

        scheduler._round_count = 0
        report = await scheduler._run_once()

        # Should not crash; should return a report (possibly with failure info)
        assert isinstance(report, RenewalRunReport)


# ─────────────────────────────────────────────
# Helper: compute expected ttl_minutes for renewal
# ─────────────────────────────────────────────


def _expected_ttl_minutes(remaining_hours: float, safety_margin: int = 1) -> int:
    """Compute the expected ttl_minutes per the renewal formula.

    Formula: int((86400 - remaining_hours * 3600) / 60) - safety_margin
    """
    return int((86400 - remaining_hours * 3600) / 60) - safety_margin


def _renewal_record(
    sandbox_id: str = "sb-1",
    source_table: str = "baas_device",
    source_id: int = 1,
    renew_fail_count: int = 0,
) -> dict:
    """Build a minimal renewal record dict for _renew_one."""
    return {
        "id": 1,
        "sandbox_id": sandbox_id,
        "source_table": source_table,
        "source_id": source_id,
        "next_renew_at": "2026-08-18 12:00:00",
        "renew_fail_count": renew_fail_count,
        "device_props": "{}",
        "hot_id": 1,
    }


def _ttl_ms(remaining_hours: float) -> int:
    """Compute ttl_timestamp in milliseconds for given remaining hours."""
    import time
    return int((time.time() + remaining_hours * 3600) * 1000)


# ═════════════════════════════════════════════
# Tests 15-30: Steps 3-5 (renewal decision,
# failure handling, report, metrics)
# ═════════════════════════════════════════════


class TestStep3RenewalDecision:
    """Tests for Step 3 — single renewal decision (5 branches)."""

    @pytest.mark.asyncio
    async def test_renewal_remaining_10h_calls_extend_ttl_with_correct_minutes(self):
        """Test 15: remaining=10h (0-12h window) → extend_ttl(839), success."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=_ttl_ms(10))
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        record = _renewal_record()
        result = await scheduler._renew_one(record)

        expected_minutes = _expected_ttl_minutes(10)
        assert expected_minutes == 839  # self-check: formula
        mock_facade.extend_ttl.assert_awaited_once_with("sb-1", 839)
        mock_repo.update_after_success.assert_called_once()
        assert result == "success"

    @pytest.mark.asyncio
    async def test_renewal_remaining_2h_calls_extend_ttl_with_correct_minutes(self):
        """Test 16: remaining=2h → extend_ttl(1319), success."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=_ttl_ms(2))
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        record = _renewal_record()
        result = await scheduler._renew_one(record)

        expected_minutes = _expected_ttl_minutes(2)
        assert expected_minutes == 1319  # self-check: formula
        mock_facade.extend_ttl.assert_awaited_once_with("sb-1", 1319)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_remaining_over_24h_skip_and_postpone(self):
        """Test 17: remaining > 24h → cannot renew, skip, postpone."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=_ttl_ms(25))
        )
        mock_facade.extend_ttl = AsyncMock()

        record = _renewal_record()
        result = await scheduler._renew_one(record)

        mock_facade.extend_ttl.assert_not_called()
        mock_repo.postpone_renewal.assert_called_once()
        assert result == "skipped"

    @pytest.mark.asyncio
    async def test_remaining_18h_in_postpone_window_skip(self):
        """Test 18: 12h < remaining <= 24h → postpone, skip."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=_ttl_ms(18))
        )
        mock_facade.extend_ttl = AsyncMock()

        record = _renewal_record()
        result = await scheduler._renew_one(record)

        mock_facade.extend_ttl.assert_not_called()
        mock_repo.postpone_renewal.assert_called_once()
        assert result == "skipped"

    @pytest.mark.asyncio
    async def test_remaining_negative_expired_enters_failure(self):
        """Test 19: remaining < 0 (expired) → failure handling, 'failed'."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=_ttl_ms(-1))
        )
        mock_facade.extend_ttl = AsyncMock()
        mock_repo.update_after_failure = MagicMock()

        record = _renewal_record()
        result = await scheduler._renew_one(record)

        mock_facade.extend_ttl.assert_not_called()
        mock_repo.update_after_failure.assert_called_once()
        assert result == "failed"

    @pytest.mark.asyncio
    async def test_get_device_info_raises_enters_failure(self):
        """Test 20: get_device_info raises Exception → failure handling."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(side_effect=Exception("timeout"))
        mock_facade.extend_ttl = AsyncMock()
        mock_repo.update_after_failure = MagicMock()

        record = _renewal_record()
        result = await scheduler._renew_one(record)

        mock_facade.extend_ttl.assert_not_called()
        mock_repo.update_after_failure.assert_called_once()
        assert result == "failed"

    @pytest.mark.asyncio
    async def test_ttl_timestamp_none_enters_failure(self):
        """Test 21: ttl_timestamp is None → failure handling."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=None)
        )
        mock_facade.extend_ttl = AsyncMock()
        mock_repo.update_after_failure = MagicMock()

        record = _renewal_record()
        result = await scheduler._renew_one(record)

        mock_facade.extend_ttl.assert_not_called()
        mock_repo.update_after_failure.assert_called_once()
        assert result == "failed"

    @pytest.mark.asyncio
    async def test_ttl_timestamp_zero_enters_failure(self):
        """Test 21b: ttl_timestamp is 0 → failure handling."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=0)
        )
        mock_facade.extend_ttl = AsyncMock()
        mock_repo.update_after_failure = MagicMock()

        record = _renewal_record()
        result = await scheduler._renew_one(record)

        mock_facade.extend_ttl.assert_not_called()
        mock_repo.update_after_failure.assert_called_once()
        assert result == "failed"

    @pytest.mark.asyncio
    async def test_extend_ttl_raises_enters_failure(self):
        """Test 22: get_device_info succeeds, but extend_ttl raises → failure."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=_ttl_ms(10))
        )
        mock_facade.extend_ttl = AsyncMock(side_effect=Exception("Arca API error"))
        mock_repo.update_after_failure = MagicMock()

        record = _renewal_record()
        result = await scheduler._renew_one(record)

        mock_repo.update_after_failure.assert_called_once()
        assert result == "failed"

    @pytest.mark.asyncio
    async def test_extend_ttl_returns_false_enters_failure(self):
        """WR-01: extend_ttl returning False WITHOUT raising (SDK rejection)
        must enter failure handling — never record success or +12h."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=_ttl_ms(10))
        )
        mock_facade.extend_ttl = AsyncMock(return_value=False)
        mock_repo.update_after_failure = MagicMock()
        mock_repo.update_after_success = MagicMock()

        record = _renewal_record()
        result = await scheduler._renew_one(record)

        mock_repo.update_after_failure.assert_called_once()
        mock_repo.update_after_success.assert_not_called()
        assert result == "failed"


class TestStep4FailureHandling:
    """Tests for Step 4 — failure handling (retry + STOPPED threshold)."""

    @pytest.mark.asyncio
    async def test_failure_retry_below_max_fail_count(self):
        """Test 23: fail_count=3 (<10) → retry, fail_count incremented to 4."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=None)
        )
        mock_repo.update_after_failure = MagicMock()
        mock_repo.set_status = MagicMock()

        record = _renewal_record(renew_fail_count=3)
        result = await scheduler._renew_one(record)

        # verify update_after_failure called with new_fail_count=4
        call_args = mock_repo.update_after_failure.call_args[0]  # positional args
        assert call_args is not None
        # source_table, source_id, next_renew_at, new_fail_count
        # args: (source_table, source_id) + kwargs: next_renew_at, new_fail_count
        call_kwargs = mock_repo.update_after_failure.call_args.kwargs
        assert call_kwargs.get("new_fail_count") == 4
        mock_repo.set_status.assert_not_called()
        assert result == "failed"

    @pytest.mark.asyncio
    async def test_failure_stopped_at_max_fail_count(self):
        """Test 24: fail_count=10 → STOPPED, no retry."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=None)
        )
        mock_repo.update_after_failure = MagicMock()
        mock_repo.set_status = MagicMock()

        record = _renewal_record(renew_fail_count=10)
        result = await scheduler._renew_one(record)

        mock_repo.set_status.assert_called_once_with(
            "test", "baas_device", 1, "STOPPED"
        )
        mock_repo.update_after_failure.assert_not_called()
        assert result == "stopped"

    @pytest.mark.asyncio
    async def test_failure_threshold_exact_nine_to_ten_triggers_stopped(self):
        """Test 25: fail_count=9 → incremented to 10 → STOPPED."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=None)
        )
        mock_repo.update_after_failure = MagicMock()
        mock_repo.set_status = MagicMock()

        record = _renewal_record(renew_fail_count=9)
        result = await scheduler._renew_one(record)

        mock_repo.set_status.assert_called_once_with(
            "test", "baas_device", 1, "STOPPED"
        )
        mock_repo.update_after_failure.assert_not_called()
        assert result == "stopped"


class TestStep5ReportAndMetrics:
    """Tests for Step 5 — report aggregation + metrics logging."""

    @pytest.mark.asyncio
    async def test_report_aggregation_counts_correctly(self):
        """Test 26: 3 records (success/skipped/failed) → correct counts."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 3
        mock_repo.count_hot_arca_devices.return_value = 3
        mock_repo.count_hot_arca_bindings.return_value = 0
        mock_repo.find_unregistered.return_value = []
        mock_repo.set_status = MagicMock()
        mock_repo.update_after_success = MagicMock()
        mock_repo.update_after_failure = MagicMock()

        rows = [
            _renewal_record("sb-1", "baas_device", 1),
            _renewal_record("sb-2", "baas_device", 2),
            _renewal_record("sb-3", "baas_device", 3),
        ]
        mock_repo.list_due_for_renewal.side_effect = [rows, []]

        # Override _renew_one to return known results
        call_count = 0

        async def _mock_renew_one(record):
            nonlocal call_count
            call_count += 1
            return ["success", "skipped", "failed"][call_count - 1]

        scheduler._renew_one = _mock_renew_one

        scheduler._round_count = 0
        report = await scheduler._run_once()

        assert report.success == 1
        assert report.skipped == 1
        assert report.failure == 1
        assert report.due_count == 3
        assert report.duration_seconds > 0
        assert isinstance(report.to_log(), str)
        assert len(report.to_log()) > 0

    @pytest.mark.asyncio
    async def test_metrics_logging_emits_structured_entries(self, caplog):
        """Test 27: after _run_once(), structured [arca_ttl_metrics] log emitted."""
        import logging

        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 3
        mock_repo.count_hot_arca_devices.return_value = 3
        mock_repo.count_hot_arca_bindings.return_value = 0
        mock_repo.find_unregistered.return_value = []
        mock_repo.set_status = MagicMock()
        mock_repo.update_after_success = MagicMock()
        mock_repo.update_after_failure = MagicMock()

        rows = [
            _renewal_record("sb-1", "baas_device", 1),
            _renewal_record("sb-2", "baas_device", 2),
            _renewal_record("sb-3", "baas_device", 3),
        ]
        mock_repo.list_due_for_renewal.side_effect = [rows, []]

        scheduler._renew_one = AsyncMock(return_value="success")

        with caplog.at_level(logging.INFO, logger="core-scheduler"):
            scheduler._round_count = 0
            await scheduler._run_once()

        # Check for metrics log entry
        metrics_logs = [r for r in caplog.records if "arca_ttl_metrics" in r.message]
        assert len(metrics_logs) >= 1
        msg = metrics_logs[0].message
        assert "last_run_timestamp" in msg
        assert "renew_failure_rate" in msg
        assert "due_count" in msg

    @pytest.mark.asyncio
    async def test_renewal_success_updates_next_renew_at_12h(self):
        """Test 28: after extend_ttl succeeds, next_renew_at = now + 12h."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=_ttl_ms(6))
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        record = _renewal_record()
        result = await scheduler._renew_one(record)

        assert result == "success"
        mock_repo.update_after_success.assert_called_once()
        # Verify next_renew_at is roughly now + 12h
        call_args = mock_repo.update_after_success.call_args[0]  # positional args
        next_renew = call_args[3]  # env, source_table, source_id, next_renew_at
        assert next_renew is not None

        from datetime import datetime, timedelta
        expected_min = datetime.now() + timedelta(hours=12) - timedelta(seconds=5)
        expected_max = datetime.now() + timedelta(hours=12) + timedelta(seconds=5)
        assert expected_min <= next_renew <= expected_max

    @pytest.mark.asyncio
    async def test_renewal_with_safety_margin_computes_correct_ttl_minutes(self):
        """Test 29: ttl_safety_margin_minutes=1 → ttl_minutes = 719 (not 720)."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(
            enabled=True,
            config_overrides={"ttl_safety_margin_minutes": 1},
        )

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=_ttl_ms(12))
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        record = _renewal_record()
        await scheduler._renew_one(record)

        # remaining=12h: ttl_minutes = int((86400 - 12*3600) / 60) - 1 = 719
        mock_facade.extend_ttl.assert_awaited_once_with("sb-1", 719)

    @pytest.mark.asyncio
    async def test_verify_facade_method_name_used(self):
        """Test 30: Verify the actual facade method names used by the scheduler.

        The scheduler should use facade.get_device_info and facade.extend_ttl
        (per RESEARCH.md code example). This test documents what the scheduler
        actually uses so future devs understand the contract.
        """
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        # Setup: get_device_info + extend_ttl both succeed
        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=_ttl_ms(6))
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        record = _renewal_record()
        result = await scheduler._renew_one(record)

        # Verify get_device_info was called (authoritative TTL lookup)
        mock_facade.get_device_info.assert_awaited_once()
        # Verify extend_ttl was called (renewal API call)
        mock_facade.extend_ttl.assert_awaited_once()
        assert result == "success"

        # Document: the scheduler uses facade.get_device_info() +
        # facade.extend_ttl(), NOT facade.update_device_ttl().
        # The facade's update_device_ttl() computes its own TTL strategy
        # internally, but the scheduler needs fine-grained control over
        # the 5-branch decision and ttl_minutes safety margin.


class TestDiscoveryScanIsolation:
    """CR-GAP-01: discovery-scan failures must never abort the round."""

    @pytest.mark.asyncio
    async def test_find_unregistered_failure_aborts_scan_but_not_round(self):
        """CR-GAP-01: find_unregistered raises → scan skipped, Step 1 runs."""
        scheduler, mock_repo, _, _ = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 0
        mock_repo.count_hot_arca_devices.return_value = 10
        mock_repo.count_hot_arca_bindings.return_value = 0  # gap=10 → scan
        mock_repo.find_unregistered.side_effect = Exception("DB down")
        mock_repo.list_due_for_renewal.return_value = []

        scheduler._round_count = 0
        report = await scheduler._run_once()

        # The due query (Step 1) still ran for both sides — the round was
        # NOT aborted by the discovery scan failure.
        assert mock_repo.list_due_for_renewal.call_count == 2
        assert isinstance(report, RenewalRunReport)

    @pytest.mark.asyncio
    async def test_row_registration_failure_skips_row_not_round(self, caplog):
        """CR-GAP-01: register_if_missing raises per-row → row skipped,
        Steps 1/2 unaffected, error counter incremented."""
        import logging

        scheduler, mock_repo, _, _ = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 0
        mock_repo.count_hot_arca_devices.return_value = 1
        mock_repo.count_hot_arca_bindings.return_value = 0  # gap=1 → scan
        call_counts: dict[str, int] = {}

        def _find_unregistered(env, side, limit=500):
            call_counts[side] = call_counts.get(side, 0) + 1
            if side == "baas_device" and call_counts[side] == 1:
                return [{
                    "id": 7,
                    "sandbox_id": "sb-poison",
                    "source_table": "baas_device",
                    "ttl": "1760000000000",
                }]
            return []

        mock_repo.find_unregistered.side_effect = _find_unregistered
        mock_repo.register_if_missing = MagicMock(side_effect=Exception("DataError"))
        mock_repo.list_due_for_renewal.return_value = []

        with caplog.at_level(logging.WARNING, logger="core-scheduler"):
            scheduler._round_count = 0
            report = await scheduler._run_once()

        assert mock_repo.list_due_for_renewal.call_count == 2
        assert report.gap_records_registered == 0
        assert isinstance(report, RenewalRunReport)
        # Per-row exception log plus the register-error summary line
        messages = [r.message for r in caplog.records]
        assert any("register_if_missing failed" in m for m in messages)
        assert any("registration error(s) this round" in m for m in messages)

    @pytest.mark.asyncio
    async def test_full_batch_registration_failure_stops_scan(self):
        """CR-GAP-01: a batch where every row fails registration stops the
        scan (poison rows would be re-fetched forever — no infinite loop)."""
        scheduler, mock_repo, _, _ = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 0
        mock_repo.count_hot_arca_devices.return_value = 1
        mock_repo.count_hot_arca_bindings.return_value = 1  # gap=2 → scan
        call_counts: dict[str, int] = {}

        def _find_unregistered(env, side, limit=500):
            call_counts[side] = call_counts.get(side, 0) + 1
            return [{
                "id": 1,
                "sandbox_id": "sb-poison",
                "source_table": side,
                "ttl": "1760000000000",
            }]

        mock_repo.find_unregistered.side_effect = _find_unregistered
        mock_repo.register_if_missing = MagicMock(side_effect=Exception("DataError"))
        mock_repo.list_due_for_renewal.return_value = []

        scheduler._round_count = 0
        report = await scheduler._run_once()

        # Exactly one fetch per side: the full-failure batch broke the
        # while loop instead of re-fetching the same poison row forever.
        assert call_counts == {"baas_device": 1, "ac_entity_device_binding": 1}
        assert mock_repo.register_if_missing.call_count == 2
        assert report.gap_records_registered == 0
        assert isinstance(report, RenewalRunReport)

    @pytest.mark.asyncio
    async def test_overflow_ttl_epoch_falls_back_to_now_plus_12h(self):
        """CR-GAP-01: a huge epoch raises OverflowError (not ValueError) —
        the broadened guard falls back to now+12h instead of escaping."""
        from datetime import datetime, timedelta

        scheduler, mock_repo, _, _ = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 0
        mock_repo.count_hot_arca_devices.return_value = 1
        mock_repo.count_hot_arca_bindings.return_value = 0  # gap=1 → scan
        calls: dict[str, int] = {}

        def _find_unregistered(env, side, limit=500):
            calls[side] = calls.get(side, 0) + 1
            if side == "baas_device" and calls[side] == 1:
                return [{
                    "id": 8,
                    "sandbox_id": "sb-huge",
                    "source_table": "baas_device",
                    "ttl": "9" * 40,  # int() fine, fromtimestamp overflows time_t
                }]
            return []

        mock_repo.find_unregistered.side_effect = _find_unregistered
        mock_repo.list_due_for_renewal.return_value = []

        scheduler._round_count = 0
        report = await scheduler._run_once()

        mock_repo.register_if_missing.assert_called_once()
        next_renew_at = mock_repo.register_if_missing.call_args.kwargs["next_renew_at"]
        expected_min = datetime.now() + timedelta(hours=12) - timedelta(seconds=5)
        expected_max = datetime.now() + timedelta(hours=12) + timedelta(seconds=5)
        assert expected_min <= next_renew_at <= expected_max
        assert report.gap_records_registered == 1


class TestProcessOneIsolation:
    """WR-GAP-01: per-record isolation — _renew_one exceptions must route to
    failure accounting instead of aborting the round via gather."""

    @pytest.mark.asyncio
    async def test_numeric_string_ttl_timestamp_is_coerced(self):
        """WR-GAP-01: numeric-string ttl_timestamp (SDK passthrough) is
        coerced to int and renewed normally — no TypeError."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=f"{_ttl_ms(10)}")
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        record = _renewal_record()
        result = await scheduler._renew_one(record)

        mock_facade.extend_ttl.assert_awaited_once()
        mock_repo.update_after_success.assert_called_once()
        assert result == "success"

    @pytest.mark.asyncio
    async def test_non_numeric_ttl_timestamp_routes_to_failure(self):
        """WR-GAP-01: non-numeric ttl_timestamp → failure accounting, no
        extend call, no uncaught TypeError."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp="tomorrow")
        )
        mock_facade.extend_ttl = AsyncMock()
        mock_repo.update_after_failure = MagicMock()

        record = _renewal_record()
        result = await scheduler._renew_one(record)

        mock_facade.extend_ttl.assert_not_called()
        mock_repo.update_after_failure.assert_called_once()
        assert result == "failed"

    @pytest.mark.asyncio
    async def test_repo_write_failure_isolated_routes_to_failure_accounting(self):
        """WR-GAP-01: update_after_success raises → record routed to failure
        accounting, round completes with failure counted."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 1
        mock_repo.count_hot_arca_devices.return_value = 1
        mock_repo.count_hot_arca_bindings.return_value = 0
        mock_repo.find_unregistered.return_value = []
        mock_repo.update_after_failure = MagicMock()
        mock_repo.update_after_success = MagicMock(side_effect=Exception("DB down"))

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=_ttl_ms(10))
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)

        rows = [_renewal_record()]
        mock_repo.list_due_for_renewal.side_effect = [rows, []]

        scheduler._round_count = 0
        report = await scheduler._run_once()

        mock_repo.update_after_failure.assert_called_once()
        assert report.failure == 1
        assert report.success == 0
        assert isinstance(report, RenewalRunReport)

    @pytest.mark.asyncio
    async def test_failure_accounting_failure_still_counts_failed(self):
        """WR-GAP-01: even failure accounting itself raises → record counted
        as failed, round completes (no gather re-raise)."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 1
        mock_repo.count_hot_arca_devices.return_value = 1
        mock_repo.count_hot_arca_bindings.return_value = 0
        mock_repo.find_unregistered.return_value = []
        mock_repo.update_after_failure = MagicMock(side_effect=Exception("DB down"))

        mock_facade.get_device_info = AsyncMock(side_effect=Exception("timeout"))

        rows = [_renewal_record()]
        mock_repo.list_due_for_renewal.side_effect = [rows, []]

        scheduler._round_count = 0
        report = await scheduler._run_once()

        # Two attempts: once inside _renew_one's get_device_info handler,
        # once from _process_one's routing — both failed, still counted.
        assert mock_repo.update_after_failure.call_count == 2
        assert report.failure == 1
        assert isinstance(report, RenewalRunReport)


class TestStoppedTransitionMetric:
    """WR-GAP-03: threshold crossing emits a scrapeable metrics line — the
    persisted STOPPED state alone is transient (revive oscillation)."""

    @pytest.mark.asyncio
    async def test_stopped_transition_emits_metrics_line(self, caplog):
        """WR-GAP-03: fail_count crossing the threshold emits
        [arca_ttl_metrics] stopped_transition=1 with fail_count."""
        import logging

        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=None)
        )
        mock_repo.set_status = MagicMock()
        mock_repo.update_after_failure = MagicMock()

        record = _renewal_record(renew_fail_count=9)
        with caplog.at_level(logging.INFO, logger="core-scheduler"):
            result = await scheduler._renew_one(record)

        mock_repo.set_status.assert_called_once_with(
            "test", "baas_device", 1, "STOPPED"
        )
        mock_repo.update_after_failure.assert_not_called()
        assert result == "stopped"

        transition_lines = [
            r.message
            for r in caplog.records
            if "stopped_transition" in r.message
        ]
        assert len(transition_lines) == 1
        msg = transition_lines[0]
        assert "[arca_ttl_metrics]" in msg
        assert "stopped_transition=1" in msg
        assert "sandbox_id=sb-1" in msg
        assert "fail_count=10" in msg