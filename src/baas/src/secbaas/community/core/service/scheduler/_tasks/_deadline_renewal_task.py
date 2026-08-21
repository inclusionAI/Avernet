"""DeadlineRenewalScheduler — implementing ScheduledTask Protocol.

Implements the full deadline-driven ARCA container TTL renewal scheduler:
  - run(): lock acquisition + dispatch to _run_once()
  - _run_once(): Steps 0-2 (gap detection, cold-table query, concurrent renewal)
  - Steps 3-5 (single renewal decision, failure handling, report) deferred to Plan 05-04.
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from uuid import uuid4

from secbaas.community.core.repository.arca_ttl import TtlRenewalScheduleRepository
from secbaas.community.core.service.distributed_lock import DistributedLockService
from secbaas.community.core.service.paas import PaasServiceFacade
from secbaas.community.core.utils.time_utils import (
    naive_utc_fromtimestamp,
    naive_utc_now,
)
from secbaas.community.logger import get_logger

from ._deadline_renewal_config import DeadlineRenewalSchedulerConfig
from ._deadline_renewal_report import GapDetectionResult, RenewalRunReport

log = get_logger("core-scheduler")

_DISCOVERY_SIDES = ("baas_device", "ac_entity_device_binding")


class DeadlineRenewalScheduler:
    """Deadline-driven ARCA container TTL renewal scheduler.

    Implements the ScheduledTask Protocol (name, interval_seconds, run)
    for registration with the AppScheduler cron lifecycle.

    The scheduler selects containers due for renewal by querying the
    baas_arca_ttl_renewal_schedule cold table rather than scanning the
    hot tables, reducing per-run query volume by >99%.

    Constructor dependencies (injected by DI container):
        config: Scheduler configuration (including engine switch).
        lock_service: Distributed lock to prevent concurrent scheduler runs.
        schedule_repo: Cold table CRUD (register, set_status, query).
        paas_facade: PaaS API facade (get_device_info, extend_ttl).
    """

    def __init__(
        self,
        config: DeadlineRenewalSchedulerConfig,
        lock_service: DistributedLockService,
        schedule_repo: TtlRenewalScheduleRepository,
        paas_facade: PaasServiceFacade,
    ) -> None:
        self._config = config
        self._lock_service = lock_service
        self._schedule_repo = schedule_repo
        self._paas_facade = paas_facade
        self._running = False
        self._round_count = 0

    @property
    def name(self) -> str:
        return "deadline_renewal_scheduler"

    @property
    def interval_seconds(self) -> int:
        return self._config.cron_interval_seconds

    @property
    def _renewal_window(self) -> timedelta:
        """Lead window before TTL expiry — half the configured TTL period.

        Derived from ``config.default_ttl_minutes`` (DI-injected from
        ``arca.default_ttl_minutes`` in _core_tasks.py), so the schedule
        targets written by the discovery scan, the postpone branches, and
        the success path all stay coherent with the configured TTL.
        Defaults: 1440 minutes -> 12h, i.e. byte-identical behavior to
        the former hardcoded ``hours=12``.
        """
        return timedelta(minutes=self._config.default_ttl_minutes // 2)

    async def run(self) -> RenewalRunReport | None:
        """Execute one scheduler run (lock acquisition + _run_once dispatch).

        Returns:
            RenewalRunReport if the run executed, None if skipped
            (disabled, already running, or lock not acquired).
        """
        if not self._config.enabled:
            return None

        if self._running:
            log.info("Scheduler already running — skipping this round")
            return None

        with self._lock_service.try_lock(
            lock_name=self._config.resolved_lock_name(),
            expire_seconds=self._config.lock_expire_seconds,
            block=False,
        ) as lock_ctx:
            if not lock_ctx.acquired:
                log.debug("Failed to acquire lock — another instance is running")
                return None

            try:
                self._running = True
                return await self._run_once()
            finally:
                self._running = False

    # ------------------------------------------------------------------
    # _run_once — main loop (Steps 0-2). Steps 3-5 deferred to Plan 05-04.
    # ------------------------------------------------------------------

    async def _run_once(
        self,
        run_uuid: str | None = None,
        trigger: str = "cron",
    ) -> RenewalRunReport:
        """Execute one full scheduler cycle (Steps 0-2).

        Args:
            run_uuid: Unique run identifier (auto-generated if None).
            trigger: What triggered this run ("cron", "manual").

        Returns:
            RenewalRunReport with per-step counters populated.
        """
        t_start = time.time()
        report = RenewalRunReport(
            run_uuid=run_uuid or str(uuid4()),
            trigger=trigger,
        )

        self._min_remaining = None  # Reset per-run metrics tracker
        self._round_count += 1

        # ---- Step 0: Gap Detection + Discovery Scan ----

        try:
            cold_count = self._schedule_repo.count_active(self._config.env)
        except Exception:
            log.exception(
                "[DeadlineRenewalScheduler] count_active failed — "
                "gap detection skipped, renewals proceed"
            )
            cold_count = None

        hot_count_device = 0
        hot_count_binding = 0
        try:
            hot_count_device = self._schedule_repo.count_hot_arca_devices(
                self._config.env
            )
            hot_count_binding = self._schedule_repo.count_hot_arca_bindings(
                self._config.env
            )
        except Exception:
            log.exception(
                "[DeadlineRenewalScheduler] Hot count query failed — discovery scan skipped"
            )

        hot_count = hot_count_device + hot_count_binding

        should_scan = False
        gap_result = None
        if cold_count is None:
            # Cold-count failure: the gap ground truth is unknown, so gap
            # detection AND the cold-table-dependent discovery scan are
            # skipped this round (see exception log above). Steps 1-2
            # (due query + renewal) run unaffected.
            report.gap_detected = False
        else:
            gap = hot_count - cold_count
            gap_result = GapDetectionResult(
                cold_count=cold_count,
                hot_count=hot_count,
                gap=gap,
            )
            report.gap_detected = gap > 0
            should_scan = (gap > 0) or (
                self._round_count % self._config.anti_join_verify_interval_cycles == 0
            )

        if should_scan and gap_result is not None:
            gap_result = await self._run_discovery_scan(gap_result)
            report.gap_records_registered = gap_result.records_registered
            report.anti_join_triggered = gap_result.anti_join_triggered
            if gap_result.register_error:
                log.warning(
                    "[DeadlineRenewalScheduler] discovery scan: %d "
                    "registration error(s) this round — failed rows skipped "
                    "(with per-row exception logs), retried next round",
                    gap_result.register_error,
                )

        # ---- Step 1: Cold Table Query + LEFT JOIN ----
        # The due gate time is computed ONCE per round as naive UTC and
        # passed to the repository as a bound parameter — the comparison
        # is then time-zone independent of the DB server clock (CR-01).
        due_now = naive_utc_now()
        # WR-05: per-side isolation — a failure on ONE source table must
        # not discard the healthy side's due batch (return_exceptions +
        # per-side handling empties only the failed side).
        results = await asyncio.gather(
            asyncio.to_thread(
                self._schedule_repo.list_due_for_renewal,
                self._config.env,
                "baas_device",
                self._config.batch_size,
                now=due_now,
            ),
            asyncio.to_thread(
                self._schedule_repo.list_due_for_renewal,
                self._config.env,
                "ac_entity_device_binding",
                self._config.batch_size,
                now=due_now,
            ),
            return_exceptions=True,
        )
        side_rows: list[list[dict]] = []
        for side, result in zip(_DISCOVERY_SIDES, results):
            if isinstance(result, BaseException):
                log.exception(
                    "[DeadlineRenewalScheduler] list_due_for_renewal failed "
                    "side=%s — this side treated as empty, the healthy "
                    "side still runs this round",
                    side,
                )
                side_rows.append([])
            else:
                side_rows.append(result or [])
        device_rows, binding_rows = side_rows

        all_rows = device_rows + binding_rows
        all_rows.sort(key=lambda r: r["next_renew_at"])
        report.due_count = len(all_rows)

        if not all_rows:
            report.duration_seconds = time.time() - t_start
            self._log_metrics(report)
            log.info("[DeadlineRenewalScheduler] %s", report.to_log())
            return report

        # ---- Orphan Detection within Step 1 ----

        processing_list: list[dict] = []
        for row in all_rows:
            if row.get("hot_id") is None:
                try:
                    self._schedule_repo.set_status(
                        self._config.env,
                        row["source_table"],
                        row["source_id"],
                        "STOPPED",
                    )
                    report.orphan_count += 1
                except Exception:
                    log.exception(
                        "[DeadlineRenewalScheduler] Failed to mark orphan "
                        "source=%s:%s as STOPPED",
                        row["source_table"],
                        row["source_id"],
                    )
            else:
                processing_list.append(row)

        # ---- Step 2: Concurrent Renewal Scaffolding ----

        if not processing_list:
            report.duration_seconds = time.time() - t_start
            self._log_metrics(report)
            log.info("[DeadlineRenewalScheduler] %s", report.to_log())
            return report

        sem = asyncio.Semaphore(self._config.max_concurrency)

        async def _process_one(record: dict) -> str:
            async with sem:
                try:
                    return await self._renew_one(record)
                except Exception:
                    log.exception(
                        "[DeadlineRenewalScheduler] record renewal raised, "
                        "routing to failure accounting: %s:%s",
                        record.get("source_table"),
                        record.get("source_id"),
                    )
                    try:
                        return await self._handle_failure(record)
                    except Exception:
                        log.exception(
                            "[DeadlineRenewalScheduler] failure accounting "
                            "failed for %s:%s — record retried next round",
                            record.get("source_table"),
                            record.get("source_id"),
                        )
                        return "failed"

        results = await asyncio.gather(
            *[_process_one(r) for r in processing_list],
        )

        for result in results:
            if result == "success":
                report.success += 1
            elif result == "skipped":
                report.skipped += 1
            elif result == "failed":
                report.failure += 1
            elif result == "stopped":
                report.stopped += 1

        report.duration_seconds = time.time() - t_start
        self._log_metrics(report)
        log.info("[DeadlineRenewalScheduler] %s", report.to_log())
        return report

    # ------------------------------------------------------------------
    # Step 0 helpers
    # ------------------------------------------------------------------

    async def _run_discovery_scan(
        self,
        gap_result: GapDetectionResult,
    ) -> GapDetectionResult:
        """Run anti-join discovery scan for unregistered ARCA containers.

        Iterates over both sides (baas_device, ac_entity_device_binding),
        calling find_unregistered() in a while loop until empty. Each
        discovered row is registered via register_if_missing().

        This is a DB-only operation — no Arca API calls.

        Failure isolation (CR-GAP-01): a find_unregistered failure aborts
        the scan for that side only, and a per-row registration failure
        skips the row — neither can abort Steps 1/2 (due query + renewals)
        of this round. Since skipped rows stay unregistered and would be
        re-fetched forever, a batch in which every row failed registration
        stops the scan for this round (the scan resumes next round).

        Args:
            gap_result: Pre-populated GapDetectionResult with counts and gap.

        Returns:
            GapDetectionResult with records_registered and
            anti_join_triggered populated.
        """
        gap_result.anti_join_triggered = True

        for side in _DISCOVERY_SIDES:
            while True:
                try:
                    rows = self._schedule_repo.find_unregistered(
                        self._config.env, side, limit=self._config.batch_size
                    )
                except Exception:
                    log.exception(
                        "[DeadlineRenewalScheduler] find_unregistered failed "
                        "side=%s — scan aborted for this round, Steps 1/2 "
                        "unaffected",
                        side,
                    )
                    break
                if not rows:
                    break

                registered_in_batch = 0
                for row in rows:
                    try:
                        ttl_ms_str = row.get("ttl")
                        if ttl_ms_str:
                            try:
                                ttl_ms = int(ttl_ms_str)
                                ttl_dt = naive_utc_fromtimestamp(ttl_ms / 1000)
                                next_renew_at = ttl_dt - self._renewal_window
                            except (ValueError, OSError, OverflowError):
                                # Unparseable ttl — fall back to now + window
                                log.warning(
                                    "[DeadlineRenewalScheduler] discovery scan: "
                                    "unparseable ttl=%s for sandbox_id=%s "
                                    "source_table=%s source_id=%s — "
                                    "falling back to now+12h",
                                    ttl_ms_str,
                                    row["sandbox_id"],
                                    row["source_table"],
                                    row["id"],
                                )
                                next_renew_at = naive_utc_now() + self._renewal_window
                        else:
                            log.info(
                                "[DeadlineRenewalScheduler] discovery scan: "
                                "missing ttl for sandbox_id=%s "
                                "source_table=%s source_id=%s — "
                                "falling back to now+12h",
                                row["sandbox_id"],
                                row["source_table"],
                                row["id"],
                            )
                            next_renew_at = naive_utc_now() + self._renewal_window

                        self._schedule_repo.register_if_missing(
                            self._config.env,
                            sandbox_id=row["sandbox_id"],
                            source_table=row["source_table"],
                            source_id=row["id"],
                            next_renew_at=next_renew_at,
                        )
                    except Exception:
                        gap_result.register_error += 1
                        log.exception(
                            "[DeadlineRenewalScheduler] register_if_missing "
                            "failed source=%s:%s sandbox_id=%s — skipped, "
                            "NOT aborting the round",
                            row["source_table"],
                            row["id"],
                            row.get("sandbox_id"),
                        )
                        continue
                    registered_in_batch += 1
                    gap_result.records_registered += 1

                if registered_in_batch == 0:
                    log.warning(
                        "[DeadlineRenewalScheduler] discovery scan side=%s: "
                        "full batch of %d rows failed registration — "
                        "stopping the scan for this round (poison rows would "
                        "be re-fetched forever); will resume next round",
                        side,
                        len(rows),
                    )
                    break

        return gap_result

    # ------------------------------------------------------------------
    # Step 2 helpers
    # ------------------------------------------------------------------

    async def _renew_one(self, record: dict) -> str:
        """Execute a single renewal decision (Step 3 in design doc §8.2).

        Returns one of: "success" | "skipped" | "failed" | "stopped"

        Decision branches (a—h):
          (a) Get authoritative TTL from Arca via facade.get_device_info()
          (b) Extract ttl_timestamp — None/0 → failed
          (c-d) Compute remaining_hours from current time
          (e) remaining < 0 (expired) → failed
          (f) remaining > 24h (cannot renew) → skipped (postpone)
          (g) 12h < remaining <= 24h (not yet due) → skipped (postpone)
          (h) 0 <= remaining <= 12h (renewal window) → extend_ttl()
        """
        sandbox_id = record.get("sandbox_id", "")

        # ---- Step 3(a): Get authoritative TTL from Arca ----
        try:
            device_info = await self._paas_facade.get_device_info(sandbox_id)
        except Exception:
            log.exception(
                "[DeadlineRenewalScheduler] get_device_info failed sandbox_id=%s",
                sandbox_id,
            )
            return await self._handle_failure(record)

        # ---- Step 3(b): Extract ttl_timestamp ----
        ttl_ms = device_info.ttl_timestamp if device_info else None

        if not ttl_ms:
            log.warning(
                "[DeadlineRenewalScheduler] ttl_timestamp empty for sandbox_id=%s",
                sandbox_id,
            )
            return await self._handle_failure(record)

        # Normalize to a numeric epoch-ms value: SDK plugins pass the wire
        # value through verbatim (enterprise _arca_sdk.py uses
        # getattr(raw_info, "ttl_timestamp", None)), so a numeric string is
        # possible — coerce it; anything else must go through failure
        # accounting rather than raise out of the arithmetic below.
        try:
            ttl_ms = int(float(ttl_ms))
        except (TypeError, ValueError, OverflowError):
            log.warning(
                "[DeadlineRenewalScheduler] sandbox_id=%s: non-numeric "
                "ttl_timestamp=%r — treated as renewal failure",
                sandbox_id,
                ttl_ms,
            )
            return await self._handle_failure(record)

        # ---- Step 3(c-d): Compute remaining_hours ----
        now_ts = time.time()
        remaining_hours = (ttl_ms / 1000.0 - now_ts) / 3600.0

        # Track minimum remaining for metrics
        if not hasattr(self, "_min_remaining") or self._min_remaining is None:
            self._min_remaining = remaining_hours
        else:
            self._min_remaining = min(self._min_remaining, remaining_hours)

        # ---- Step 3(e): Expired (remaining < 0) ----
        if remaining_hours < 0:
            log.warning(
                "[DeadlineRenewalScheduler] sandbox_id=%s TTL expired "
                "(remaining=%.1fh)",
                sandbox_id,
                remaining_hours,
            )
            return await self._handle_failure(record)

        # ---- Step 3(f): remaining > 24h — cannot renew (API constraint) ----
        if remaining_hours > 24:
            expiration_dt = naive_utc_fromtimestamp(ttl_ms / 1000.0)
            next_renew = expiration_dt - self._renewal_window
            self._schedule_repo.postpone_renewal(
                self._config.env,
                record["source_table"],
                record["source_id"],
                next_renew,
            )
            log.info(
                "[DeadlineRenewalScheduler] sandbox_id=%s remaining=%.1fh > 24h, "
                "postponed to %s",
                sandbox_id,
                remaining_hours,
                next_renew.isoformat(),
            )
            return "skipped"

        # ---- Step 3(g): 12h < remaining <= 24h — not yet due ----
        if remaining_hours > self._config.renew_threshold_hours:
            expiration_dt = naive_utc_fromtimestamp(ttl_ms / 1000.0)
            next_renew = expiration_dt - self._renewal_window
            self._schedule_repo.postpone_renewal(
                self._config.env,
                record["source_table"],
                record["source_id"],
                next_renew,
            )
            log.info(
                "[DeadlineRenewalScheduler] sandbox_id=%s remaining=%.1fh "
                "in (%.1f, 24] — postponed to %s",
                sandbox_id,
                remaining_hours,
                self._config.renew_threshold_hours,
                next_renew.isoformat(),
            )
            return "skipped"

        # ---- Step 3(h): 0 <= remaining <= 12h — renewal window ----
        # TTL period comes from the configured default_ttl_minutes (1440:
        # identical to the former 86400-second constant); the safety margin
        # is subtracted so an extension never lands exactly on the expiry.
        ttl_minutes = (
            int((self._config.default_ttl_minutes * 60 - remaining_hours * 3600) / 60)
            - self._config.ttl_safety_margin_minutes
        )

        try:
            renewed = await self._paas_facade.extend_ttl(sandbox_id, ttl_minutes)
        except Exception:
            log.exception(
                "[DeadlineRenewalScheduler] extend_ttl failed sandbox_id=%s "
                "ttl_minutes=%d",
                sandbox_id,
                ttl_minutes,
            )
            return await self._handle_failure(record)

        if not renewed:
            # Rejected extension (SDK returned a literal False / success=False)
            # without raising — treat as failure, never record success or push
            # next_renew_at 12h out for a TTL that was not extended.
            log.warning(
                "[DeadlineRenewalScheduler] extend_ttl rejected sandbox_id=%s "
                "ttl_minutes=%d",
                sandbox_id,
                ttl_minutes,
            )
            return await self._handle_failure(record)

        # Renewal success
        next_renew = naive_utc_now() + self._renewal_window
        self._schedule_repo.update_after_success(
            self._config.env, record["source_table"], record["source_id"], next_renew
        )
        log.info(
            "[DeadlineRenewalScheduler] sandbox_id=%s renewed "
            "ttl_minutes=%d next_renew=%s",
            sandbox_id,
            ttl_minutes,
            next_renew.isoformat(),
        )
        return "success"

    # ------------------------------------------------------------------
    # Step 4: Failure handling
    # ------------------------------------------------------------------

    async def _handle_failure(self, record: dict) -> str:
        """Handle renewal failure with retry and STOPPED threshold.

        Args:
            record: The schedule record (dict from list_due_for_renewal).

        Returns:
            "failed" or "stopped".
        """
        new_fail_count = record.get("renew_fail_count", 0) + 1

        if new_fail_count >= self._config.max_fail_count:
            self._schedule_repo.set_status(
                self._config.env, record["source_table"], record["source_id"], "STOPPED"
            )
            log.error(
                "[DeadlineRenewalScheduler] sandbox_id=%s "
                "source=%s:%s reached max_fail_count=%d, marked STOPPED",
                record.get("sandbox_id"),
                record["source_table"],
                record["source_id"],
                self._config.max_fail_count,
            )
            # The persisted STOPPED state is transient: the next round's
            # discovery scan can revive threshold-STOPPED rows (the
            # anti-join matches only ACTIVE cold rows and
            # register_if_missing upserts back to ACTIVE), so the durable
            # alarm signal is this metrics line, not the row status.
            log.info(
                "[arca_ttl_metrics] stopped_transition=1 sandbox_id=%s "
                "source_table=%s source_id=%s fail_count=%d",
                record.get("sandbox_id"),
                record["source_table"],
                record["source_id"],
                new_fail_count,
            )
            return "stopped"

        # Retry: schedule next attempt after retry_delay_minutes
        next_retry = naive_utc_now() + timedelta(
            minutes=self._config.retry_delay_minutes
        )
        self._schedule_repo.update_after_failure(
            self._config.env,
            record["source_table"],
            record["source_id"],
            next_renew_at=next_retry,
            new_fail_count=new_fail_count,
        )
        log.warning(
            "[DeadlineRenewalScheduler] sandbox_id=%s fail_count=%d/%d, retry at %s",
            record.get("sandbox_id"),
            new_fail_count,
            self._config.max_fail_count,
            next_retry.isoformat(),
        )
        return "failed"

    # ------------------------------------------------------------------
    # Step 5: Metrics logging
    # ------------------------------------------------------------------

    def _log_metrics(self, report: RenewalRunReport) -> None:
        """Emit structured metrics as [arca_ttl_metrics] log entries.

        Logs 4 Prometheus-aligned metrics plus auxiliary:
          - arca_ttl_last_run_timestamp: Current Unix timestamp (Gauge)
          - arca_ttl_remaining_hours: Minimum remaining TTL hours (Histogram)
          - arca_ttl_renew_failure_rate: failure / (success+failure) ratio
          - arca_ttl_schedule_due_count: Number of due records processed
          - arca_ttl_gap_detected: Whether a gap was found this run
        """
        min_remaining = getattr(self, "_min_remaining", None)
        remaining_val = min_remaining if min_remaining is not None else -1

        total_decisions = max(report.success + report.failure, 1)
        failure_rate = report.failure / total_decisions

        log.info(
            "[arca_ttl_metrics] last_run_timestamp=%d remaining_hours_min=%.1f "
            "renew_failure_rate=%.3f due_count=%d gap_detected=%d",
            int(time.time()),
            remaining_val,
            failure_rate,
            report.due_count,
            1 if report.gap_detected else 0,
        )
