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

import logging
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
from secbaas.community.core.service.scheduler._tasks._deadline_renewal_task import (
    _requested_ttl_minutes,
)
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.core.utils.time_utils import format_ttl_expiration_time


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
    cfg_kwargs = {
        "enabled": enabled,
        "batch_size": 500,
        "max_concurrency": 20,
        "anti_join_verify_interval_cycles": 48,
        "env": "test",
    }
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
            enabled=True,
            lock_acquired=False,
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
        assert (
            kwargs["lock_name"] == f"{scheduler._config.lock_name}_{get_current_env()}"
        )
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
                return [
                    {
                        "id": 1,
                        "sandbox_id": "sb-1",
                        "source_table": "baas_device",
                        "ttl": "1760000000000",
                    }
                ]
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
    async def test_terminal_stopped_gap_never_reregisters(self):
        """85-02: a persistent gap post-fix scans empty and registers nothing.

        Terminal model: a threshold-STOPPED row stays out of the discovery
        anti-join result (any-status suppression), so the gap never collapses
        (cold < hot forever) and the resurrect loop's write — register_if_missing
        — never fires.
        """
        scheduler, mock_repo, _, _ = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 45000  # cold
        mock_repo.count_hot_arca_devices.return_value = 40000
        mock_repo.count_hot_arca_bindings.return_value = 10000  # hot total = 50000
        mock_repo.list_due_for_renewal.return_value = []
        # Post-fix model: every suppressed STOPPED row stays out of discovery,
        # so the scan comes back empty on both sides.
        mock_repo.find_unregistered.return_value = []
        mock_repo.register_if_missing = MagicMock()

        scheduler._round_count = 0
        report = await scheduler._run_once()

        # The scan still ran for the persistent gap...
        assert mock_repo.find_unregistered.call_count >= 1
        # ...but the gap does NOT collapse — terminal rows keep cold < hot.
        assert report.gap_detected is True
        assert report.gap_records_registered == 0
        # The resurrect loop's write never fires.
        assert mock_repo.register_if_missing.call_count == 0

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
        assert (
            mock_repo.find_unregistered.call_count == call_count_before
        )  # no new calls

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

    @pytest.mark.asyncio
    async def test_cold_count_failure_skips_gap_detection_but_continues_round(
        self, caplog
    ):
        """WR-01: count_active raising must not abort the round — gap
        detection + discovery scan skipped, Steps 1-2 renewals proceed."""
        import logging

        scheduler, mock_repo, _, _ = _make_scheduler(enabled=True)
        mock_repo.count_active.side_effect = Exception("cold table down")
        mock_repo.count_hot_arca_devices.return_value = 1
        mock_repo.count_hot_arca_bindings.return_value = 0
        mock_repo.find_unregistered.return_value = []
        mock_repo.list_due_for_renewal.side_effect = [
            [
                {
                    "id": 1,
                    "sandbox_id": "sb-1",
                    "source_table": "baas_device",
                    "source_id": 1,
                    "next_renew_at": "2026-08-18 12:00:00",
                    "renew_fail_count": 0,
                    "device_props": "{}",
                    "hot_id": 1,
                }
            ],
            [],
        ]
        scheduler._renew_one = AsyncMock(return_value="success")

        # Round 47 → 48: the periodic anti-join verify would normally fire
        # here — with an unknown cold count it must be suppressed as well.
        scheduler._round_count = 47
        with caplog.at_level(logging.ERROR, logger="core-scheduler"):
            report = await scheduler._run_once()

        assert isinstance(report, RenewalRunReport)
        assert report.gap_detected is False
        mock_repo.find_unregistered.assert_not_called()
        # Renewals still ran — the round was NOT aborted.
        assert scheduler._renew_one.call_count == 1
        assert report.success == 1
        assert report.due_count == 1
        messages = [r.message for r in caplog.records]
        assert any("count_active failed" in m for m in messages)


class TestStep1ColdTableQuery:
    """Tests for Step 1 — cold table query + LEFT JOIN orphan detection."""

    def _due_row(
        self, source_table="baas_device", source_id=1, sandbox_id="sb-1", hot_id=1
    ):
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
            [
                self._due_row("ac_entity_device_binding", 101, "sb-101", hot_id=101)
                for _ in range(100)
            ],
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
        mock_repo.set_status.assert_called_once_with(
            "test", "baas_device", 2, "STOPPED"
        )
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

    @pytest.mark.asyncio
    async def test_one_side_query_failure_keeps_healthy_side_rows(self):
        """WR-05: when one due query raises, the healthy side's batch is
        still processed this round — only the failed side is emptied."""
        scheduler, mock_repo, _, _ = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 200
        mock_repo.count_hot_arca_devices.return_value = 100
        mock_repo.count_hot_arca_bindings.return_value = 100
        mock_repo.find_unregistered.return_value = []
        mock_repo.list_due_for_renewal.side_effect = [
            Exception("device table down"),
            [self._due_row("ac_entity_device_binding", 101, "sb-101", hot_id=101)],
        ]
        scheduler._renew_one = AsyncMock(return_value="success")

        scheduler._round_count = 0
        report = await scheduler._run_once()

        assert mock_repo.list_due_for_renewal.call_count == 2
        assert report.due_count == 1
        assert report.success == 1
        assert isinstance(report, RenewalRunReport)


class TestEarlyReturnMetrics:
    """WR-02: metrics + summary must be emitted on EVERY round, including
    the early-return paths (empty due list / all-orphan list) — monitoring
    must not go dark exactly when the scheduler is idle."""

    def _due_row(
        self, source_table="baas_device", source_id=1, sandbox_id="sb-1", hot_id=1
    ):
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
    async def test_empty_due_list_early_return_emits_metrics_and_summary(self, caplog):
        """WR-02: zero due rows → the round still emits [arca_ttl_metrics]
        and the [DeadlineRenewalScheduler] summary line."""
        import logging

        scheduler, mock_repo, _, _ = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 200
        mock_repo.count_hot_arca_devices.return_value = 200
        mock_repo.count_hot_arca_bindings.return_value = 0
        mock_repo.list_due_for_renewal.return_value = []
        mock_repo.find_unregistered.return_value = []

        scheduler._round_count = 0
        with caplog.at_level(logging.INFO, logger="core-scheduler"):
            report = await scheduler._run_once()

        assert report.due_count == 0
        assert report.duration_seconds > 0
        messages = [r.message for r in caplog.records]
        assert any("[arca_ttl_metrics]" in m and "due_count=0" in m for m in messages)
        assert any("[DeadlineRenewalScheduler]" in m for m in messages)

    @pytest.mark.asyncio
    async def test_all_orphan_round_early_return_emits_metrics_and_summary(
        self, caplog
    ):
        """WR-02: every due row is an orphan (empty processing list) →
        metrics + summary still emitted, orphan count reported."""
        import logging

        scheduler, mock_repo, _, _ = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 2
        mock_repo.count_hot_arca_devices.return_value = 1
        mock_repo.count_hot_arca_bindings.return_value = 1
        mock_repo.find_unregistered.return_value = []
        mock_repo.list_due_for_renewal.side_effect = [
            [
                self._due_row("baas_device", 1, "sb-1", hot_id=None),
                self._due_row("baas_device", 2, "sb-2", hot_id=None),
            ],
            [],
        ]

        scheduler._round_count = 0
        with caplog.at_level(logging.INFO, logger="core-scheduler"):
            report = await scheduler._run_once()

        assert report.due_count == 2
        assert report.orphan_count == 2
        assert report.success == 0
        messages = [r.message for r in caplog.records]
        assert any("[arca_ttl_metrics]" in m and "due_count=2" in m for m in messages)
        assert any("[DeadlineRenewalScheduler]" in m for m in messages)
        mock_repo.set_status.assert_called()


class TestStep2ConcurrentRenewalScaffolding:
    """Tests for Step 2 — asyncio.Semaphore + _renew_one placeholder."""

    def _due_row(
        self, source_table="baas_device", source_id=1, sandbox_id="sb-1", hot_id=1
    ):
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

        rows = [
            self._due_row("baas_device", i, f"sb-{i}", hot_id=i) for i in range(1, 4)
        ]
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

        # WR-03: step (a) read, then the post-extend TTL re-read.
        mock_facade.get_device_info = AsyncMock(
            side_effect=[
                MagicMock(ttl_timestamp=_ttl_ms(10)),
                MagicMock(ttl_timestamp=_ttl_ms(10) + 839 * 60 * 1000),
            ]
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        record = _renewal_record()
        result = await scheduler._renew_one(record)

        expected_minutes = _expected_ttl_minutes(10)
        assert expected_minutes == 839  # self-check: formula
        mock_facade.extend_ttl.assert_awaited_once_with("sb-1", 839)
        assert mock_facade.get_device_info.await_count == 2
        mock_repo.update_after_success.assert_called_once()
        assert result == "success"

    @pytest.mark.asyncio
    async def test_renewal_remaining_2h_calls_extend_ttl_with_correct_minutes(self):
        """Test 16: remaining=2h → extend_ttl(1319), success."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        # WR-03: step (a) read, then the post-extend TTL re-read.
        mock_facade.get_device_info = AsyncMock(
            side_effect=[
                MagicMock(ttl_timestamp=_ttl_ms(2)),
                MagicMock(ttl_timestamp=_ttl_ms(2) + 1319 * 60 * 1000),
            ]
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
    async def test_get_device_info_raises_enters_failure(self, caplog):
        """Test 20: get_device_info raises Exception → failure handling."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(side_effect=Exception("timeout"))
        mock_facade.extend_ttl = AsyncMock()
        mock_repo.update_after_failure = MagicMock()

        record = _renewal_record()
        with caplog.at_level(logging.WARNING, logger="core-scheduler"):
            result = await scheduler._renew_one(record)

        mock_facade.extend_ttl.assert_not_called()
        mock_repo.update_after_failure.assert_called_once()
        assert result == "failed"

        failure_lines = [
            r for r in caplog.records if "get_device_info failed" in r.message
        ]
        assert len(failure_lines) == 1
        assert failure_lines[0].levelno == logging.WARNING
        assert "timeout" in failure_lines[0].message

    @pytest.mark.asyncio
    async def test_get_device_info_failure_logs_debug_traceback(self, caplog):
        """WR-01: get_device_info failure keeps a DEBUG exc_info traceback."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(side_effect=Exception("timeout"))
        mock_facade.extend_ttl = AsyncMock()
        mock_repo.update_after_failure = MagicMock()

        caplog.set_level(logging.DEBUG, logger="core-scheduler")
        record = _renewal_record()
        result = await scheduler._renew_one(record)

        assert result == "failed"

        matching = [r for r in caplog.records if "get_device_info failed" in r.message]
        warning_records = [r for r in matching if r.levelno == logging.WARNING]
        debug_records = [r for r in matching if r.levelno == logging.DEBUG]
        assert len(warning_records) == 1
        assert len(debug_records) == 1

        warning = warning_records[0]
        assert "timeout" in warning.message
        assert warning.exc_info is None

        debug = debug_records[0]
        assert "timeout" in debug.message
        assert debug.exc_info is not None
        assert debug.exc_info[0] is Exception

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

        mock_facade.get_device_info = AsyncMock(return_value=MagicMock(ttl_timestamp=0))
        mock_facade.extend_ttl = AsyncMock()
        mock_repo.update_after_failure = MagicMock()

        record = _renewal_record()
        result = await scheduler._renew_one(record)

        mock_facade.extend_ttl.assert_not_called()
        mock_repo.update_after_failure.assert_called_once()
        assert result == "failed"

    @pytest.mark.asyncio
    async def test_extend_ttl_raises_enters_failure(self, caplog):
        """Test 22: get_device_info succeeds, but extend_ttl raises → failure."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=_ttl_ms(10))
        )
        mock_facade.extend_ttl = AsyncMock(side_effect=Exception("Arca API error"))
        mock_repo.update_after_failure = MagicMock()

        record = _renewal_record()
        with caplog.at_level(logging.WARNING, logger="core-scheduler"):
            result = await scheduler._renew_one(record)

        mock_repo.update_after_failure.assert_called_once()
        assert result == "failed"

        failure_lines = [r for r in caplog.records if "extend_ttl failed" in r.message]
        assert len(failure_lines) == 1
        assert failure_lines[0].levelno == logging.WARNING
        assert "Arca API error" in failure_lines[0].message
        assert "ttl_minutes=" in failure_lines[0].message

    @pytest.mark.asyncio
    async def test_extend_ttl_failure_logs_debug_traceback(self, caplog):
        """WR-01: extend_ttl failure keeps a DEBUG exc_info traceback."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=_ttl_ms(10))
        )
        mock_facade.extend_ttl = AsyncMock(side_effect=Exception("Arca API error"))
        mock_repo.update_after_failure = MagicMock()

        caplog.set_level(logging.DEBUG, logger="core-scheduler")
        record = _renewal_record()
        result = await scheduler._renew_one(record)

        assert result == "failed"

        matching = [r for r in caplog.records if "extend_ttl failed" in r.message]
        warning_records = [r for r in matching if r.levelno == logging.WARNING]
        debug_records = [r for r in matching if r.levelno == logging.DEBUG]
        assert len(warning_records) == 1
        assert len(debug_records) == 1

        warning = warning_records[0]
        assert "Arca API error" in warning.message
        assert "ttl_minutes=" in warning.message
        assert warning.exc_info is None

        debug = debug_records[0]
        assert "extend_ttl failed" in debug.message
        assert "ttl_minutes=" in debug.message
        assert debug.exc_info is not None
        assert debug.exc_info[0] is Exception

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


class TestTtlWindowDerivation:
    """WR-02 (user-adjudicated scheme): the lead window and the ttl_minutes
    period are derived from config.default_ttl_minutes (DI-injected from
    arca.default_ttl_minutes), not hardcoded — with the 1440 default the
    behavior is byte-identical to the former hardcoded 12h/86400 values."""

    @pytest.mark.asyncio
    async def test_success_half_life_when_post_extend_remaining_within_window(self):
        """D2/D-02: default_ttl_minutes=2880 -> window is 24h; a post-extend
        re-read of 18h lands inside the window, so next_renew_at is the
        half-life target now + max(R'/2, cron) = now + 9h (the 30min cron
        floor is dominated by 9h), instead of the old E' - window target
        which would land ~6h behind now."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(
            enabled=True,
            config_overrides={"default_ttl_minutes": 2880},
        )

        mock_facade.get_device_info = AsyncMock(
            side_effect=[
                MagicMock(ttl_timestamp=_ttl_ms(6)),
                MagicMock(ttl_timestamp=_ttl_ms(18)),  # honest post-extend value
            ]
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        result = await scheduler._renew_one(_renewal_record())

        assert result == "success"
        next_renew = mock_repo.update_after_success.call_args[0][3]
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        now_cst = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        expected_min = now_cst + timedelta(hours=9) - timedelta(seconds=5)
        expected_max = now_cst + timedelta(hours=9) + timedelta(seconds=5)
        assert expected_min <= next_renew <= expected_max

    @pytest.mark.asyncio
    async def test_renew_target_derives_from_default_ttl_minutes(self):
        """EG-4/D-03: with default_ttl_minutes=2880 the derived threshold is
        2880//2 = 1440min = 24h, so remaining=18h falls INTO the renewal
        window (h) — extend_ttl is called with m = 1799 and next_renew
        derives from the post-extend re-read (R'=42h > window 24h →
        D2 status quo: next = E' - window ≈ now + 18h)."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(
            enabled=True,
            config_overrides={"default_ttl_minutes": 2880},
        )

        ttl_ms = _ttl_ms(18)
        mock_facade.get_device_info = AsyncMock(
            side_effect=[
                MagicMock(ttl_timestamp=ttl_ms),
                MagicMock(ttl_timestamp=_ttl_ms(42)),
            ]
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        result = await scheduler._renew_one(_renewal_record())

        assert result == "success"
        mock_facade.extend_ttl.assert_awaited_once_with("sb-1", 1799)
        mock_repo.postpone_renewal.assert_not_called()
        next_renew = mock_repo.update_after_success.call_args[0][3]
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        now_cst = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        expected_min = now_cst + timedelta(hours=18) - timedelta(seconds=5)
        expected_max = now_cst + timedelta(hours=18) + timedelta(seconds=5)
        assert expected_min <= next_renew <= expected_max

    @pytest.mark.asyncio
    async def test_ttl_minutes_formula_uses_configured_period(self):
        """default_ttl_minutes=2880, remaining=12h -> extend_ttl(2159):
        the 86400 constant is replaced by default_ttl_minutes*60."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(
            enabled=True,
            config_overrides={"default_ttl_minutes": 2880},
        )

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=_ttl_ms(12))
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        await scheduler._renew_one(_renewal_record())

        # int((2880*60 - 12*3600)/60) - 1 = int(2160) - 1 = 2159
        mock_facade.extend_ttl.assert_awaited_once_with("sb-1", 2159)

    @pytest.mark.asyncio
    async def test_default_ttl_below_derived_threshold_postpones_to_expiry_minus_window(
        self,
    ):
        """EG-4/D-03: default_ttl_minutes=600 -> derived threshold is
        600//2 = 300min = 5h, so remaining=10h falls into the (g) postpone
        branch — postpone_renewal gets expiry - window (≈ now + 5h) and
        extend_ttl is never called. The WR-03 1-minute clamp keeps honest
        coverage via the module-level _requested_ttl_minutes helper tests
        (84-01 Task 2, Open Question 3 option b)."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(
            enabled=True,
            config_overrides={"default_ttl_minutes": 600},
        )

        ttl_ms = _ttl_ms(10)
        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=ttl_ms)
        )
        mock_facade.extend_ttl = AsyncMock()

        result = await scheduler._renew_one(_renewal_record())

        assert result == "skipped"
        mock_facade.extend_ttl.assert_not_awaited()
        next_renew = mock_repo.postpone_renewal.call_args[0][3]
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        now_cst = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        expected_min = now_cst + timedelta(hours=5) - timedelta(seconds=5)
        expected_max = now_cst + timedelta(hours=5) + timedelta(seconds=5)
        assert expected_min <= next_renew <= expected_max

    @pytest.mark.asyncio
    async def test_discovery_register_target_derives_from_default_ttl_minutes(self):
        """default_ttl_minutes=2880 -> discovery register = ttl_utc - 24h."""
        scheduler, mock_repo, _, _ = _make_scheduler(
            enabled=True,
            config_overrides={"default_ttl_minutes": 2880},
        )
        mock_repo.count_active.return_value = 0
        mock_repo.count_hot_arca_devices.return_value = 1
        mock_repo.count_hot_arca_bindings.return_value = 0  # gap=1 -> scan
        mock_repo.list_due_for_renewal.return_value = []
        ttl_epoch_sec = 1760000000

        calls: dict[str, int] = {}

        def _find_unregistered(env, side, limit=500):
            calls[side] = calls.get(side, 0) + 1
            if side == "baas_device" and calls[side] == 1:
                return [
                    {
                        "id": 1,
                        "sandbox_id": "sb-1",
                        "source_table": "baas_device",
                        "ttl": str(ttl_epoch_sec * 1000),
                    }
                ]
            return []

        mock_repo.find_unregistered.side_effect = _find_unregistered

        scheduler._round_count = 0
        await scheduler._run_once()

        mock_repo.register_if_missing.assert_called_once()
        next_renew_at = mock_repo.register_if_missing.call_args.kwargs["next_renew_at"]
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        ttl_utc = datetime.fromtimestamp(
            ttl_epoch_sec, tz=ZoneInfo("Asia/Shanghai")
        ).replace(tzinfo=None)
        expected_min = ttl_utc - timedelta(hours=24) - timedelta(seconds=5)
        expected_max = ttl_utc - timedelta(hours=24) + timedelta(seconds=5)
        assert expected_min <= next_renew_at <= expected_max


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

        async def _mock_renew_one(record, run_uuid=None):
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

    def test_report_declares_anti_join_triggered_field(self):
        """WR-01: anti_join_triggered is a declared dataclass field, so
        structured consumers (asdict / serializers) preserve the flag
        instead of silently dropping the dynamic attribute."""
        from dataclasses import asdict

        report = RenewalRunReport()
        report.anti_join_triggered = True
        assert asdict(report)["anti_join_triggered"] is True
        # The field is declared: constructing with it is also legal.
        assert RenewalRunReport(anti_join_triggered=True).anti_join_triggered is True

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
    async def test_renewal_success_derives_next_renew_from_post_extend_ttl(self):
        """Test 28 (WR-03): after extend_ttl succeeds, next_renew_at derives
        from the post-extend TTL re-read (expiry - window), not the assumed
        now+12h — Arca clamps extensions at its 24h remaining cap, so the
        assumption can land after the real expiry."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        post_extend_ms = _ttl_ms(18)  # platform clamped to 18h remaining
        mock_facade.get_device_info = AsyncMock(
            side_effect=[
                MagicMock(ttl_timestamp=_ttl_ms(6)),
                MagicMock(ttl_timestamp=post_extend_ms),
            ]
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        record = _renewal_record()
        result = await scheduler._renew_one(record)

        assert result == "success"
        mock_repo.update_after_success.assert_called_once()
        # Verify next_renew_at is roughly clamped_expiry - 12h (≈ now + 6h),
        # NOT the old assumption of now + 12h.
        call_args = mock_repo.update_after_success.call_args[0]  # positional args
        next_renew = call_args[3]  # env, source_table, source_id, next_renew_at
        assert next_renew is not None

        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        # CR-01: the scheduler writes fixed Asia/Shanghai (+08:00, no DST)
        # pipeline timestamps, so the expected window is the +08:00 wall
        # clock, never the host-local wall clock.
        expiration_cst = datetime.fromtimestamp(
            post_extend_ms / 1000.0, tz=ZoneInfo("Asia/Shanghai")
        ).replace(tzinfo=None)
        expected_min = expiration_cst - timedelta(hours=12) - timedelta(seconds=5)
        expected_max = expiration_cst - timedelta(hours=12) + timedelta(seconds=5)
        assert expected_min <= next_renew <= expected_max
        # Clamping asymmetry lock: the derived target stays well under the
        # old assumed now+12h target (post-extend expiry is only 18h out).
        now_cst = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        assert next_renew <= now_cst + timedelta(hours=10)

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
            side_effect=[
                MagicMock(ttl_timestamp=_ttl_ms(6)),
                MagicMock(ttl_timestamp=_ttl_ms(6) + 1079 * 60 * 1000),
            ]
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        record = _renewal_record()
        result = await scheduler._renew_one(record)

        # Verify get_device_info was called (authoritative TTL lookup) —
        # twice: step (a) plus the WR-03 post-extend re-read.
        assert mock_facade.get_device_info.await_count == 2
        # Verify extend_ttl was called (renewal API call)
        mock_facade.extend_ttl.assert_awaited_once()
        assert result == "success"

        # Document: the scheduler uses facade.get_device_info() +
        # facade.extend_ttl(), NOT facade.update_device_ttl().
        # The facade's update_device_ttl() computes its own TTL strategy
        # internally, but the scheduler needs fine-grained control over
        # the 5-branch decision and ttl_minutes safety margin.


class TestPostExtendNextRenewDerivation:
    """WR-03: the success path derives next_renew_at from the authoritative
    post-extend TTL re-read instead of assuming now + renewal_window (the
    assumed target can land after the real expiry when Arca clamps the
    extension at its 24h remaining cap)."""

    @pytest.mark.asyncio
    async def test_success_status_quo_when_post_extend_remaining_exceeds_window(self):
        """D2/D-02: default_ttl_minutes=2880 -> window 24h; a post-extend
        re-read of 36h exceeds the window, so the D2 status-quo target
        holds: next_renew_at = E' - window ≈ now + 12h. The fixture sits
        far from the R' == window boundary (Pitfall 1) so the branch choice
        is deterministic."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(
            enabled=True,
            config_overrides={"default_ttl_minutes": 2880},
        )
        # remaining=2h -> extend request = int((2880*60 - 2*3600)/60) - 1
        # = 2759 min; the re-read returns 36h (clearly above the 24h window).
        mock_facade.get_device_info = AsyncMock(
            side_effect=[
                MagicMock(ttl_timestamp=_ttl_ms(2)),
                MagicMock(ttl_timestamp=_ttl_ms(36)),
            ]
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        result = await scheduler._renew_one(_renewal_record())

        assert result == "success"
        mock_facade.extend_ttl.assert_awaited_once_with("sb-1", 2759)
        next_renew = mock_repo.update_after_success.call_args[0][3]
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        now_cst = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        expected_min = now_cst + timedelta(hours=12) - timedelta(seconds=5)
        expected_max = now_cst + timedelta(hours=12) + timedelta(seconds=5)
        assert expected_min <= next_renew <= expected_max

    @pytest.mark.asyncio
    async def test_success_post_extend_reread_failure_short_rescans(self):
        """WR-03: when the post-extend TTL re-read fails, next_renew_at is a
        conservative short rescan interval (cron_interval_seconds) — the
        next round re-derives from the platform via step (a) — never the
        unsafe now+window push."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            side_effect=[
                MagicMock(ttl_timestamp=_ttl_ms(6)),
                Exception("post-extend read timeout"),
            ]
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        result = await scheduler._renew_one(_renewal_record())

        assert result == "success"
        mock_repo.update_after_success.assert_called_once()
        next_renew = mock_repo.update_after_success.call_args[0][3]
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        now_cst = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        expected = now_cst + timedelta(seconds=scheduler._config.cron_interval_seconds)
        assert (
            expected - timedelta(seconds=5)
            <= next_renew
            <= expected + timedelta(seconds=5)
        )


class TestPostExtendConsistencyWatermark:
    """D1/D-01: the post-extend TTL re-read must never exceed the pre-extend
    expiry plus the requested extension minutes plus the locked 5-minute
    tolerance — an optimistic echo (platform clamped the TTL but reported
    the request) violates the bound and is rejected into the conservative
    short rescan, with a post_extend_ttl_inconsistent=1 metrics line and a
    dash digest ttl_after."""

    @staticmethod
    def _digest_lines(caplog):
        return [
            r.getMessage()
            for r in caplog.records
            if r.name == "arca-renew-digest"
            and r.getMessage().startswith("ttl_renew_digest,")
        ]

    @pytest.mark.asyncio
    async def test_reject_reread_above_expected_plus_tol_short_rescans_with_metric_and_dash(
        self, caplog
    ):
        """D1 end-to-end: re-read = expected + 30min > tol 5min → rejected
        into the short rescan (next_renew = now + cron_interval), the
        post_extend_ttl_inconsistent=1 metric fires, and the digest keeps
        result="success" with ttl_after="-". The 30-minute overflow is
        deterministic and independent of wall-clock drift, and the fallback
        target doubles as the EG-1 floor."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        pre_read = _ttl_ms(10)
        expected_minutes = _expected_ttl_minutes(10)  # 839 under TTL 1440
        re_read = pre_read + expected_minutes * 60_000 + 30 * 60_000
        mock_facade.get_device_info = AsyncMock(
            side_effect=[
                MagicMock(ttl_timestamp=pre_read),
                MagicMock(ttl_timestamp=re_read),
            ]
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        caplog.set_level(logging.INFO, logger="core-scheduler")
        caplog.set_level(logging.INFO, logger="arca-renew-digest")
        result = await scheduler._renew_one(_renewal_record())

        assert result == "success"
        # (a) The scrapeable inconsistency metric line.
        metric_lines = [
            r.message
            for r in caplog.records
            if "post_extend_ttl_inconsistent" in r.message
        ]
        assert len(metric_lines) == 1
        assert "[arca_ttl_metrics]" in metric_lines[0]
        assert "post_extend_ttl_inconsistent=1" in metric_lines[0]
        assert "sandbox_id=sb-1" in metric_lines[0]
        # (b) Digest contract: success result, dash ttl_after.
        lines = self._digest_lines(caplog)
        assert len(lines) == 1
        fields = lines[0].split(",")
        assert fields[6] == "success"
        assert fields[8] == "-"
        # (c) Conservative short rescan: now + cron_interval.
        next_renew = mock_repo.update_after_success.call_args[0][3]
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        now_cst = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        expected = now_cst + timedelta(seconds=scheduler._config.cron_interval_seconds)
        assert (
            expected - timedelta(seconds=5)
            <= next_renew
            <= expected + timedelta(seconds=5)
        )

    @pytest.mark.asyncio
    async def test_reread_four_minutes_over_expected_accepted(self, caplog):
        """D1 tol boundary (accept side): re-read = expected + 4min < tol
        5min → trusted; R' ≈ 24h3m > window 12h → D2 status quo gives
        next ≈ now + 12h3m (E' = now + 24h3m minus the 12h window), an
        accepted digest without the inconsistency metric. Fixture math is
        drift-immune: both sides compute m=1079 from the same 6h pre-read."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        pre_read = _ttl_ms(6)
        expected_minutes = _expected_ttl_minutes(6)  # 1079 under TTL 1440
        re_read = pre_read + expected_minutes * 60_000 + 4 * 60_000
        mock_facade.get_device_info = AsyncMock(
            side_effect=[
                MagicMock(ttl_timestamp=pre_read),
                MagicMock(ttl_timestamp=re_read),
            ]
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        caplog.set_level(logging.INFO, logger="core-scheduler")
        caplog.set_level(logging.INFO, logger="arca-renew-digest")
        result = await scheduler._renew_one(_renewal_record())

        assert result == "success"
        messages = [r.message for r in caplog.records]
        assert not any("post_extend_ttl_inconsistent" in m for m in messages)
        lines = self._digest_lines(caplog)
        assert len(lines) == 1
        fields = lines[0].split(",")
        assert fields[6] == "success"
        assert fields[8] != "-"
        next_renew = mock_repo.update_after_success.call_args[0][3]
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        now_cst = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        expected = now_cst + timedelta(hours=12, minutes=3)
        assert (
            expected - timedelta(seconds=5)
            <= next_renew
            <= expected + timedelta(seconds=5)
        )

    @pytest.mark.asyncio
    async def test_reread_six_minutes_over_expected_rejected(self, caplog):
        """D1 tol boundary (reject side): re-read = expected + 6min > tol
        5min → rejected: metric line fires, digest ttl_after dash, short
        rescan next ≈ now + cron_interval."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        pre_read = _ttl_ms(6)
        expected_minutes = _expected_ttl_minutes(6)  # 1079 under TTL 1440
        re_read = pre_read + expected_minutes * 60_000 + 6 * 60_000
        mock_facade.get_device_info = AsyncMock(
            side_effect=[
                MagicMock(ttl_timestamp=pre_read),
                MagicMock(ttl_timestamp=re_read),
            ]
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        caplog.set_level(logging.INFO, logger="core-scheduler")
        caplog.set_level(logging.INFO, logger="arca-renew-digest")
        result = await scheduler._renew_one(_renewal_record())

        assert result == "success"
        metric_lines = [
            r.message
            for r in caplog.records
            if "post_extend_ttl_inconsistent" in r.message
        ]
        assert len(metric_lines) == 1
        lines = self._digest_lines(caplog)
        assert len(lines) == 1
        fields = lines[0].split(",")
        assert fields[6] == "success"
        assert fields[8] == "-"
        next_renew = mock_repo.update_after_success.call_args[0][3]
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        now_cst = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        expected = now_cst + timedelta(seconds=scheduler._config.cron_interval_seconds)
        assert (
            expected - timedelta(seconds=5)
            <= next_renew
            <= expected + timedelta(seconds=5)
        )


class TestD2SchedulingBranches:
    """D2/D-02 half-life and EG-1 floor: within the window the success
    target is now + max(R'/2, cron); any branch result below now + cron is
    lifted to the floor. All fixtures keep the post-extend re-read clearly
    inside one branch (Pitfall 1)."""

    @pytest.mark.asyncio
    async def test_half_life_uses_now_plus_half_of_remaining(self, caplog):
        """R' = 8h ≤ window 12h → half-life: next ≈ now + 8h/2 = now + 4h;
        no inconsistency metric, digest ttl_after formatted."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            side_effect=[
                MagicMock(ttl_timestamp=_ttl_ms(6)),
                MagicMock(ttl_timestamp=_ttl_ms(8)),
            ]
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        caplog.set_level(logging.INFO, logger="core-scheduler")
        caplog.set_level(logging.INFO, logger="arca-renew-digest")
        result = await scheduler._renew_one(_renewal_record())

        assert result == "success"
        messages = [r.message for r in caplog.records]
        assert not any("post_extend_ttl_inconsistent" in m for m in messages)
        digest_lines = [
            r.getMessage()
            for r in caplog.records
            if r.name == "arca-renew-digest"
            and r.getMessage().startswith("ttl_renew_digest,")
        ]
        assert digest_lines[0].split(",")[8] != "-"
        next_renew = mock_repo.update_after_success.call_args[0][3]
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        now_cst = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        expected = now_cst + timedelta(hours=4)
        assert (
            expected - timedelta(seconds=5)
            <= next_renew
            <= expected + timedelta(seconds=5)
        )

    @pytest.mark.asyncio
    async def test_half_life_lifted_to_cron_floor(self):
        """R' = 18min ≤ window 12h → half-life max(R'/2=9min, cron=30min)
        = 30min → next ≈ now + 30min (the half-life branch's own floor)."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            side_effect=[
                MagicMock(ttl_timestamp=_ttl_ms(6)),
                MagicMock(ttl_timestamp=_ttl_ms(0.3)),
            ]
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        result = await scheduler._renew_one(_renewal_record())

        assert result == "success"
        next_renew = mock_repo.update_after_success.call_args[0][3]
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        now_cst = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        expected = now_cst + timedelta(minutes=30)
        assert (
            expected - timedelta(seconds=5)
            <= next_renew
            <= expected + timedelta(seconds=5)
        )

    @pytest.mark.asyncio
    async def test_status_quo_lifted_to_cron_floor(self):
        """R' = 12.2h > window 12h → status quo next = E' - window ≈ now +
        12min < now + cron(30min) → EG-1 floor lifts to now + 30min — the
        original churn defect scenario EG-1 was raised for."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        mock_facade.get_device_info = AsyncMock(
            side_effect=[
                MagicMock(ttl_timestamp=_ttl_ms(6)),
                MagicMock(ttl_timestamp=_ttl_ms(12.2)),
            ]
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        result = await scheduler._renew_one(_renewal_record())

        assert result == "success"
        next_renew = mock_repo.update_after_success.call_args[0][3]
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        now_cst = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        expected = now_cst + timedelta(minutes=30)
        assert (
            expected - timedelta(seconds=5)
            <= next_renew
            <= expected + timedelta(seconds=5)
        )


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
                return [
                    {
                        "id": 7,
                        "sandbox_id": "sb-poison",
                        "source_table": "baas_device",
                        "ttl": "1760000000000",
                    }
                ]
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
            return [
                {
                    "id": 1,
                    "sandbox_id": "sb-poison",
                    "source_table": side,
                    "ttl": "1760000000000",
                }
            ]

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
                return [
                    {
                        "id": 8,
                        "sandbox_id": "sb-huge",
                        "source_table": "baas_device",
                        "ttl": "9" * 40,  # int() fine, fromtimestamp overflows time_t
                    }
                ]
            return []

        mock_repo.find_unregistered.side_effect = _find_unregistered
        mock_repo.list_due_for_renewal.return_value = []

        scheduler._round_count = 0
        report = await scheduler._run_once()

        mock_repo.register_if_missing.assert_called_once()
        next_renew_at = mock_repo.register_if_missing.call_args.kwargs["next_renew_at"]
        # CR-01: discovery-scan fallback writes the fixed Asia/Shanghai
        # (+08:00, no DST) wall clock, not host-local time.
        from zoneinfo import ZoneInfo

        now_utc = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        expected_min = now_utc + timedelta(hours=12) - timedelta(seconds=5)
        expected_max = now_utc + timedelta(hours=12) + timedelta(seconds=5)
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
    """WR-GAP-03: threshold crossing emits a scrapeable metrics line — after
    phase 85's any-status anti-join, threshold-STOPPED is terminal (revival
    only via the device-lifecycle register() upsert); the metrics line is the
    durable alarm."""

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
            r.message for r in caplog.records if "stopped_transition" in r.message
        ]
        assert len(transition_lines) == 1
        msg = transition_lines[0]
        assert "[arca_ttl_metrics]" in msg
        assert "stopped_transition=1" in msg
        assert "sandbox_id=sb-1" in msg
        assert "fail_count=10" in msg

        transition_records = [
            r for r in caplog.records if "stopped_transition" in r.message
        ]
        assert len(transition_records) == 1
        assert transition_records[0].levelno == logging.INFO

        stopped_lines = [r for r in caplog.records if "marked STOPPED" in r.message]
        assert len(stopped_lines) == 1
        assert stopped_lines[0].levelno == logging.WARNING


class TestRenewalDigestLogging:
    """REN-07: ttl_renew_digest CSV emission from every terminal branch.

    The deadline engine shares the arca-renew-digest logger and CSV contract
    with the legacy SandboxDeviceRouter digest, so the monitor pipeline sees
    a homogeneous renewal digest stream across both engines during the
    pre-gray-release window. Assertions split message by comma and check
    the field positions of the 9-field contract line.
    """

    @staticmethod
    def _digest_lines(caplog):
        return [
            r.getMessage()
            for r in caplog.records
            if r.name == "arca-renew-digest"
            and r.getMessage().startswith("ttl_renew_digest,")
        ]

    @pytest.mark.asyncio
    async def test_success_branch_emits_digest_line(self, caplog):
        """h3 success: fields [1..9] = ttl_renew_digest, uuid, renew, 1,
        baas, sb-1, success, formatted ttl_before, formatted ttl_after."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        ttl_before_ms = _ttl_ms(10)
        ttl_after_ms = ttl_before_ms + 839 * 60 * 1000  # WR-03 post-extend re-read
        mock_facade.get_device_info = AsyncMock(
            side_effect=[
                MagicMock(ttl_timestamp=ttl_before_ms),
                MagicMock(ttl_timestamp=ttl_after_ms),
            ]
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        caplog.set_level(logging.INFO, logger="arca-renew-digest")
        result = await scheduler._renew_one(_renewal_record())

        assert result == "success"
        lines = self._digest_lines(caplog)
        assert len(lines) == 1
        fields = lines[0].split(",")
        assert len(fields) == 9
        assert fields[0] == "ttl_renew_digest"
        assert fields[1]  # auto-generated uuid when run_uuid omitted
        assert fields[2] == "renew"
        assert fields[3] == "1"
        assert fields[4] == "baas"
        assert fields[5] == "sb-1"
        assert fields[6] == "success"
        # Digest contract normalizes spaces to dashes inside TTL fields.
        assert fields[7] == format_ttl_expiration_time(ttl_before_ms).replace(" ", "-")
        assert fields[8] == format_ttl_expiration_time(ttl_after_ms).replace(" ", "-")

    @pytest.mark.asyncio
    async def test_over_24h_skip_branch_emits_digest_line(self, caplog):
        """f-skip: ..., skipped, ttl_before formatted, ttl_after dash."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)
        ttl_ms = _ttl_ms(25)
        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=ttl_ms)
        )
        mock_facade.extend_ttl = AsyncMock()

        caplog.set_level(logging.INFO, logger="arca-renew-digest")
        result = await scheduler._renew_one(_renewal_record())

        assert result == "skipped"
        mock_facade.extend_ttl.assert_not_awaited()
        lines = self._digest_lines(caplog)
        assert len(lines) == 1
        fields = lines[0].split(",")
        assert fields[6] == "skipped"
        # Digest contract normalizes spaces to dashes inside TTL fields.
        assert fields[7] == format_ttl_expiration_time(ttl_ms).replace(" ", "-")
        assert fields[8] == "-"

    @pytest.mark.asyncio
    async def test_get_device_info_failure_branch_emits_digest_line(self, caplog):
        """a-failure below max_fail_count: ..., failure, -, -."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)
        mock_facade.get_device_info = AsyncMock(side_effect=Exception("timeout"))
        mock_repo.update_after_failure = MagicMock()

        caplog.set_level(logging.INFO, logger="arca-renew-digest")
        result = await scheduler._renew_one(_renewal_record())

        assert result == "failed"
        lines = self._digest_lines(caplog)
        assert len(lines) == 1
        fields = lines[0].split(",")
        # Strict legacy vocabulary: the "failed" outcome projects to "failure".
        assert fields[6] == "failure"
        assert fields[7] == "-"
        assert fields[8] == "-"

    @pytest.mark.asyncio
    async def test_pathological_ttl_digest_format_failure_uses_placeholder(
        self, caplog
    ):
        """ME-01: a numeric-but-pathological ttl_timestamp (huge negative
        epoch overflowing the datetime range) flows through failure
        accounting exactly once; the digest TTL formatter's failure
        degrades to the legacy "-" placeholders instead of raising out of
        _renew_one."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)

        pathological_ms = -(10**19)
        # Setup guard: the chosen value really does raise when formatted
        # (the concrete exception class varies by platform).
        with pytest.raises((OverflowError, OSError, ValueError)):
            format_ttl_expiration_time(float(pathological_ms))

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=pathological_ms)
        )
        mock_facade.extend_ttl = AsyncMock()
        mock_repo.update_after_failure = MagicMock()

        caplog.set_level(logging.INFO, logger="arca-renew-digest")
        result = await scheduler._renew_one(_renewal_record())

        # (a) The pathological TTL never propagates an exception.
        assert result == "failed"
        # (b) No double accounting: failure handling ran exactly once — the
        # guarded digest formatting did not re-route the record through
        # _process_one's failure fallback.
        mock_facade.extend_ttl.assert_not_awaited()
        mock_repo.update_after_failure.assert_called_once()
        # (c) The digest row exists with legacy "-" TTL placeholders.
        lines = self._digest_lines(caplog)
        assert len(lines) == 1
        fields = lines[0].split(",")
        # Strict legacy vocabulary: the "failed" outcome projects to "failure".
        assert fields[6] == "failure"
        assert fields[7] == "-"
        assert fields[8] == "-"
        # The formatter failure is warned on core-scheduler, never emitted
        # into the digest stream.
        assert any("digest ttl format failed" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_stopped_outcome_maps_to_failure_digest_result(self, caplog):
        """Threshold STOPPED maps the digest result to "failure" (not
        "stopped") — monitor vocabulary stays two-valued success/failure."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(
            enabled=True, config_overrides={"max_fail_count": 1}
        )
        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=None)
        )
        mock_repo.set_status = MagicMock()

        caplog.set_level(logging.INFO, logger="arca-renew-digest")
        result = await scheduler._renew_one(_renewal_record(renew_fail_count=0))

        assert result == "stopped"
        lines = self._digest_lines(caplog)
        assert len(lines) == 1
        assert lines[0].split(",")[6] == "failure"

    @pytest.mark.asyncio
    async def test_ac_binding_source_table_maps_to_ac_binding_table_type(self, caplog):
        """ac_entity_device_binding maps digest table_type to ac_binding."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)
        ttl_before_ms = _ttl_ms(10)
        mock_facade.get_device_info = AsyncMock(
            side_effect=[
                MagicMock(ttl_timestamp=ttl_before_ms),
                MagicMock(ttl_timestamp=ttl_before_ms + 839 * 60 * 1000),
            ]
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)
        mock_repo.update_after_success = MagicMock()

        caplog.set_level(logging.INFO, logger="arca-renew-digest")
        result = await scheduler._renew_one(
            _renewal_record(source_table="ac_entity_device_binding")
        )

        assert result == "success"
        lines = self._digest_lines(caplog)
        assert len(lines) == 1
        assert lines[0].split(",")[4] == "ac_binding"

    @pytest.mark.asyncio
    async def test_run_uuid_threaded_into_digest_second_field(self, caplog):
        """An explicit run_uuid becomes the digest second field verbatim."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)
        mock_facade.get_device_info = AsyncMock(side_effect=Exception("timeout"))
        mock_repo.update_after_failure = MagicMock()

        caplog.set_level(logging.INFO, logger="arca-renew-digest")
        await scheduler._renew_one(_renewal_record(), run_uuid="u-mr")

        lines = self._digest_lines(caplog)
        assert len(lines) == 1
        assert lines[0].split(",")[1] == "u-mr"

    @pytest.mark.asyncio
    async def test_missing_run_uuid_falls_back_to_fresh_uuid(self, caplog):
        """Direct invocation without run_uuid falls back to a fresh uuid4
        (byte-identical behavior to the legacy renew_ttl path)."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)
        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=None)
        )
        mock_repo.update_after_failure = MagicMock()

        caplog.set_level(logging.INFO, logger="arca-renew-digest")
        await scheduler._renew_one(_renewal_record())

        lines = self._digest_lines(caplog)
        assert len(lines) == 1
        second = lines[0].split(",")[1]
        assert second and second != "None"

    @pytest.mark.asyncio
    async def test_process_one_routes_renewal_raise_to_failed_digest(self, caplog):
        """A _renew_one raise routed to failure accounting still emits a
        digest line projecting the failed outcome to "failure" — no silent
        terminal path."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 1
        mock_repo.count_hot_arca_devices.return_value = 1
        mock_repo.count_hot_arca_bindings.return_value = 0
        mock_repo.find_unregistered.return_value = []
        mock_repo.update_after_failure = MagicMock()
        # update_after_success raises → _renew_one raises out of the h3 path
        mock_repo.update_after_success = MagicMock(side_effect=Exception("DB down"))

        mock_facade.get_device_info = AsyncMock(
            return_value=MagicMock(ttl_timestamp=_ttl_ms(10))
        )
        mock_facade.extend_ttl = AsyncMock(return_value=True)

        mock_repo.list_due_for_renewal.side_effect = [[_renewal_record()], []]

        caplog.set_level(logging.INFO, logger="arca-renew-digest")
        scheduler._round_count = 0
        report = await scheduler._run_once()

        assert report.failure == 1
        lines = self._digest_lines(caplog)
        assert len(lines) == 1
        fields = lines[0].split(",")
        # Strict legacy vocabulary: the "failed" outcome projects to "failure".
        assert fields[6] == "failure"
        assert fields[7] == "-"
        assert fields[8] == "-"

    @pytest.mark.asyncio
    async def test_process_one_failure_accounting_raise_still_emits_failed_digest(
        self, caplog
    ):
        """Even failure accounting raising still emits a digest line
        projecting the failed outcome to "failure" — the second-level
        fallback is not silent."""
        scheduler, mock_repo, _, mock_facade = _make_scheduler(enabled=True)
        mock_repo.count_active.return_value = 1
        mock_repo.count_hot_arca_devices.return_value = 1
        mock_repo.count_hot_arca_bindings.return_value = 0
        mock_repo.find_unregistered.return_value = []
        mock_repo.update_after_failure = MagicMock(side_effect=Exception("DB down"))

        mock_facade.get_device_info = AsyncMock(side_effect=Exception("timeout"))

        mock_repo.list_due_for_renewal.side_effect = [[_renewal_record()], []]

        caplog.set_level(logging.INFO, logger="arca-renew-digest")
        scheduler._round_count = 0
        report = await scheduler._run_once()

        assert report.failure == 1
        lines = self._digest_lines(caplog)
        assert len(lines) == 1
        fields = lines[0].split(",")
        # Strict legacy vocabulary: the "failed" outcome projects to "failure".
        assert fields[6] == "failure"
        assert fields[7] == "-"
        assert fields[8] == "-"


class TestRequestedTtlMinutes:
    """WR-03: the requested-minutes clamp lives in the module-level
    _requested_ttl_minutes helper so the 1-minute floor keeps honest unit
    coverage even though the derived threshold (EG-4) makes the negative
    input unreachable via _renew_one."""

    def test_normal_derivation(self):
        assert _requested_ttl_minutes(1440, 6.0, 1) == 1079

    def test_large_ttl_derivation(self):
        assert _requested_ttl_minutes(2880, 18.0, 1) == 1799

    def test_negative_clamped_to_one(self):
        assert _requested_ttl_minutes(600, 10.0, 1) == 1

    def test_zero_clamped_to_one(self):
        assert _requested_ttl_minutes(600, 10.0, 0) == 1

    def test_safety_margin_subtracted(self):
        assert _requested_ttl_minutes(1440, 6.0, 30) == (
            int((1440 * 60 - 6 * 3600) / 60) - 30
        )
