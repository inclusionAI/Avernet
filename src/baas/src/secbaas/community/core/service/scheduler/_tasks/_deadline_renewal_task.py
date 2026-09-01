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

from secbaas.community.api.device_manage import DeviceFacadeException
from secbaas.community.core.repository.arca_ttl import TtlRenewalScheduleRepository
from secbaas.community.core.service.distributed_lock import DistributedLockService
from secbaas.community.core.service.paas import (
    ErrorCode,
    PaasError,
    PaasServiceFacade,
)
from secbaas.community.core.utils import log_renew_digest
from secbaas.community.core.utils.time_utils import (
    format_ttl_expiration_time,
    naive_cst_fromtimestamp,
    naive_cst_now,
    renewal_window,
)
from secbaas.community.logger import get_logger

from ._deadline_renewal_config import DeadlineRenewalSchedulerConfig
from ._deadline_renewal_report import GapDetectionResult, RenewalRunReport

log = get_logger("core-scheduler")

_DISCOVERY_SIDES = ("baas_device", "ac_entity_device_binding")

# ttl_renew_digest field mapping: the digest CSV keeps the legacy table_type
# vocabulary (baas / ac_binding) and the digest result column projects
# strictly into the legacy alarm vocabulary {success, skipped, failure} —
# both the transient "stopped" outcome and the ordinary "failed" outcome
# fold into the monitor's "failure" bucket.
_DIGEST_TABLE_TYPE = {"baas_device": "baas", "ac_entity_device_binding": "ac_binding"}
_DIGEST_RESULT = {"stopped": "failure", "failed": "failure"}


def _digest_ttl(ms: int | None) -> str | None:
    """Format an epoch-ms TTL for the digest stream, never raising.

    Digest emission is best-effort by contract: a value that cannot be
    formatted (numeric garbage from a device API passthrough) degrades to
    the legacy "-" placeholder instead of interrupting the renewal flow.
    """
    if ms is None:
        return None
    try:
        return format_ttl_expiration_time(float(ms))
    except (TypeError, ValueError, OSError, OverflowError):
        log.warning("[DeadlineRenewalScheduler] digest ttl format failed for %r", ms)
        return None


def _requested_ttl_minutes(
    default_ttl_minutes: int,
    remaining_hours: float,
    ttl_safety_margin_minutes: int,
) -> int:
    """Requested extension minutes for one renewal (WR-03 clamp).

    Full-TTL target minus the currently-remaining lead, with the safety
    margin subtracted and a 1-minute floor. The clamp lives in this
    module-level helper so it keeps honest unit coverage even though the
    derived threshold (EG-4) makes the negative input unreachable via
    _renew_one.
    """
    return max(
        1,
        int((default_ttl_minutes * 60 - remaining_hours * 3600) / 60)
        - ttl_safety_margin_minutes,
    )


def _is_confirmed_gone(exc: BaseException) -> bool:
    """Confirm only the platform's dead-sandbox error class.

    True only when the exception is a DeviceFacadeException whose
    original_error is a PaasError with code DEVICE_NOT_FOUND — the error
    shape the platform emits for a genuinely recycled sandbox. Every other
    shape (DEVICE_UNAVAILABLE, COMMAND_TIMEOUT, raw network exceptions,
    anything that is not a DeviceFacadeException) returns False, so an
    unknown failure can never silently kill a live container.
    """
    if not isinstance(exc, DeviceFacadeException):
        return False
    original = exc.original_error
    return (
        isinstance(original, PaasError) and original.code == ErrorCode.DEVICE_NOT_FOUND
    )


class DeadlineRenewalScheduler:
    """Deadline-driven ARCA container TTL renewal scheduler.

    Implements the ScheduledTask Protocol (name, interval_seconds, run)
    for registration with the AppScheduler cron lifecycle.

    The scheduler selects containers due for renewal by querying the
    baas_bot_ttl_renewal_schedule cold table rather than scanning the
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
        Shares ``time_utils.renewal_window`` with the lifecycle wrapper so
        both writers stay in lock-step (WR-02). Default: 1440 minutes ->
        12h, byte-identical to the former hardcoded ``hours=12``.
        """
        return renewal_window(self._config.default_ttl_minutes)

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
        hot_counts_degraded = False
        try:
            hot_count_device = self._schedule_repo.count_hot_arca_devices(
                self._config.env
            )
            hot_count_binding = self._schedule_repo.count_hot_arca_bindings(
                self._config.env
            )
        except Exception:
            # WR-02 (85-86 deep review): a partial hot-count failure must be
            # distinguishable from a genuine zero gap — the residual
            # hot_count=0 would fabricate a negative covered gap and read
            # as "no gap, no discovery". Degrade the gap math this round
            # instead; the periodic anti-join verify remains the discovery
            # channel so hoisted unregistered rows are never hidden.
            hot_counts_degraded = True
            log.exception(
                "[DeadlineRenewalScheduler] Hot count query failed — gap "
                "detection degraded this round (discovery limited to the "
                "periodic anti-join verify)"
            )

        hot_count = hot_count_device + hot_count_binding

        # R3 covered math: covered = hot rows matched by ANY cold row
        # (ACTIVE or STOPPED); suppressed = the STOPPED-covered subset.
        # A raised count degrades to the legacy formula with a warning —
        # never a crash (the warning keeps the degradation observable).
        covered_hot: int | None = None
        suppressed_terminal = 0
        try:
            covered_hot = self._schedule_repo.count_hot_covered(self._config.env)
            suppressed_terminal = self._schedule_repo.count_suppressed_terminal(
                self._config.env
            )
        except Exception:
            log.warning(
                "[DeadlineRenewalScheduler] covered-count query failed — "
                "gap falls back to legacy hot-minus-cold"
            )
        report.suppressed_terminal_count = suppressed_terminal

        # WR-02 (85-86 deep review): the periodic anti-join verify is the
        # gap-independent discovery channel — it must stay reachable even
        # when the gap math itself is skipped (hot-count degradation), so
        # hoisted unregistered rows can never be hidden by a counting
        # failure. Cold-count failure (below) still suppresses it, matching
        # the WR-01 pinned contract.
        should_scan = False
        gap_result = None
        periodic_verify = (
            self._round_count % self._config.anti_join_verify_interval_cycles == 0
        )
        if cold_count is None:
            # Cold-count failure: the gap ground truth is unknown, so gap
            # detection AND the cold-table-dependent discovery scan are
            # skipped this round (see exception log above). Steps 1-2
            # (due query + renewal) run unaffected.
            report.gap_detected = False
        elif hot_counts_degraded:
            # WR-02 (85-86 deep review): hot counts failed while the cold
            # count succeeded — the residual hot_count=0 can only fabricate
            # a negative covered gap (0 - covered < 0), which silently reads
            # as "no gap", indistinguishable from a healthy zero-gap round.
            # Skip the gap math this round (no negative gap is ever
            # computed) and keep only the gap-independent periodic
            # anti-join verify alive (see the hot-count exception log
            # above for the distinguishing signal).
            report.gap_detected = False
            should_scan = periodic_verify
            if should_scan:
                # The gap is unknowable — hand the scan a bare result holder
                # (hot/gap keep their 0 defaults and carry no gap semantics)
                # so the verify still registers hoisted rows.
                gap_result = GapDetectionResult(cold_count=cold_count)
        else:
            if covered_hot is not None:
                gap = hot_count - covered_hot
            else:
                gap = hot_count - cold_count
            gap_result = GapDetectionResult(
                cold_count=cold_count,
                hot_count=hot_count,
                gap=gap,
            )
            report.gap_detected = gap > 0
            should_scan = (gap > 0) or periodic_verify

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
        # The due gate time is computed ONCE per round as naive
        # Asia/Shanghai (+08:00, no DST) and passed to the repository as
        # a bound parameter — the comparison is then time-zone
        # independent of the DB server clock (CR-01).
        due_now = naive_cst_now()
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
        # R4 (WR-02) hot-row recheck: hot_id IS NULL can also mean the hot
        # row simply reappeared since the due JOIN ran. Before writing the
        # terminal STOPPED, re-check hot-row existence with the same JOIN
        # conditions: reappeared (or recheck-failed — never STOP on doubt)
        # rows postpone; genuinely absent rows write STOPPED stamped
        # stop_reason='orphan'.

        processing_list: list[dict] = []
        for row in all_rows:
            if row.get("hot_id") is None:
                # Alive on recheck / failed recheck → postpone (never STOP
                # a possibly-live row); False → terminal STOPPED.
                alive: bool | None = None
                try:
                    alive = self._schedule_repo.hot_row_exists(
                        self._config.env,
                        row["source_table"],
                        row["source_id"],
                    )
                except Exception:
                    log.warning(
                        "[DeadlineRenewalScheduler] hot_row_exists recheck "
                        "failed source=%s:%s — postponing instead of STOPPED "
                        "(never STOP on doubt)",
                        row["source_table"],
                        row["source_id"],
                    )

                if alive is False:
                    try:
                        self._schedule_repo.set_status(
                            self._config.env,
                            row["source_table"],
                            row["source_id"],
                            "STOPPED",
                            stop_reason="orphan",
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
                    next_renew = naive_cst_now() + timedelta(
                        minutes=self._config.retry_delay_minutes
                    )
                    # WR-01 (86-REVIEW): the postpone write is a per-row
                    # low-risk op — a single poison-row failure must not
                    # abort the remaining orphan checks and this round's
                    # metrics (CR-GAP-01 discipline).
                    try:
                        self._schedule_repo.postpone_renewal(
                            self._config.env,
                            row["source_table"],
                            row["source_id"],
                            next_renew,
                        )
                    except Exception:
                        log.exception(
                            "[DeadlineRenewalScheduler] Failed to postpone "
                            "orphan source=%s:%s after recheck",
                            row["source_table"],
                            row["source_id"],
                        )
                    if alive is True:
                        log.info(
                            "[DeadlineRenewalScheduler] orphan source=%s:%s "
                            "hot row reappeared — postponed instead of STOPPED",
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
                    return await self._renew_one(record, run_uuid=report.run_uuid)
                except Exception:
                    log.exception(
                        "[DeadlineRenewalScheduler] record renewal raised, "
                        "routing to failure accounting: %s:%s",
                        record.get("source_table"),
                        record.get("source_id"),
                    )
                    try:
                        r = await self._handle_failure(record)
                        self._emit_renew_digest(record, r, run_uuid=report.run_uuid)
                        return r
                    except Exception:
                        log.exception(
                            "[DeadlineRenewalScheduler] failure accounting "
                            "failed for %s:%s — record retried next round",
                            record.get("source_table"),
                            record.get("source_id"),
                        )
                        self._emit_renew_digest(
                            record, "failed", run_uuid=report.run_uuid
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
                                ttl_dt = naive_cst_fromtimestamp(ttl_ms / 1000)
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
                                next_renew_at = naive_cst_now() + self._renewal_window
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
                            next_renew_at = naive_cst_now() + self._renewal_window

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

    def _emit_renew_digest(
        self,
        record: dict,
        result: str,
        *,
        ttl_before_ms: int | None = None,
        ttl_after_ms: int | None = None,
        run_uuid: str | None = None,
    ) -> None:
        """Emit one ttl_renew_digest CSV line for a terminal renewal outcome.

        Threads the run uuid from _run_once's report (uuid4 fallback for
        direct invocation — same behavior as the legacy renew_ttl path),
        maps source_table to the legacy digest table_type vocabulary and
        the "stopped"/"failed" outcomes to the legacy "failure" bucket,
        and formats platform
        epoch-ms TTLs via the fixed +08:00 wall clock so the digest stream
        is homogeneous with the health_check scanner's — dash placeholder
        when a TTL is unknown.

        Best-effort by design: log_renew_digest swallows its own logging
        failures, so digest emission can never affect the renewal flow.
        """
        before = _digest_ttl(ttl_before_ms)
        after = _digest_ttl(ttl_after_ms)
        log_renew_digest(
            run_uuid=run_uuid or str(uuid4()),
            table_id=record.get("source_id", 0),
            table_type=_DIGEST_TABLE_TYPE.get(
                record.get("source_table", ""), record.get("source_table", "")
            ),
            arca_device_id=record.get("sandbox_id", ""),
            result=_DIGEST_RESULT.get(result, result),
            ttl_before=before,
            ttl_after=after,
        )

    async def _renew_one(self, record: dict, run_uuid: str | None = None) -> str:
        """Execute a single renewal decision (Step 3 in design doc §8.2).

        Returns one of: "success" | "skipped" | "failed" | "stopped"
        (digest result column folds "failed"/"stopped" into legacy "failure").

        Every terminal branch emits a ttl_renew_digest CSV line on the
        arca-renew-digest logger (via _emit_renew_digest) so the monitor
        pipeline sees the same renewal digest stream as the legacy
        SandboxDeviceRouter path.

        Args:
            record: The schedule record (dict from list_due_for_renewal).
            run_uuid: Run identifier threaded from _run_once's report for
                the digest emission; falls back to a fresh uuid4 when
                omitted (direct invocation, tests).

        Decision branches (a—h):
          (a) Get authoritative TTL from Arca via facade.get_device_info()
          (b) Extract ttl_timestamp — None/0 → failed
          (c-d) Compute remaining_hours from current time
          (e) remaining < 0 (expired) → failed
          (f) remaining > 24h (cannot renew) → skipped (postpone)
          (g) 12h < remaining <= 24h (not yet due) → skipped (postpone)
          (h) 0 <= remaining <= 12h (renewal window) → extend_ttl(); on
              success next_renew_at is derived from the post-extend TTL
              re-read (WR-03: Arca clamps extensions at its 24h remaining
              cap, so assuming now + window can overshoot the real expiry)

        Failure branches route through _handle_failure with a liveness
        verdict — only platform-confirmed gone (DEVICE_NOT_FOUND) or
        expired (remaining < 0) verdicts can terminate at the cap.
        """
        sandbox_id = record.get("sandbox_id", "")

        # ---- Step 3(a): Get authoritative TTL from Arca ----
        try:
            device_info = await self._paas_facade.get_device_info(sandbox_id)
        except Exception as e:
            log.warning(
                "[DeadlineRenewalScheduler] get_device_info failed sandbox_id=%s: %s",
                sandbox_id,
                e,
            )
            log.debug(
                "[DeadlineRenewalScheduler] get_device_info failed sandbox_id=%s: %s",
                sandbox_id,
                e,
                exc_info=True,
            )
            outcome = await self._handle_failure(
                record,
                stop_reason=("threshold_gone" if _is_confirmed_gone(e) else None),
            )
            self._emit_renew_digest(record, outcome, run_uuid=run_uuid)
            return outcome

        # ---- Step 3(b): Extract ttl_timestamp ----
        ttl_ms = device_info.ttl_timestamp if device_info else None

        if not ttl_ms:
            log.warning(
                "[DeadlineRenewalScheduler] ttl_timestamp empty for sandbox_id=%s",
                sandbox_id,
            )
            outcome = await self._handle_failure(record)
            self._emit_renew_digest(record, outcome, run_uuid=run_uuid)
            return outcome

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
            outcome = await self._handle_failure(record)
            self._emit_renew_digest(record, outcome, run_uuid=run_uuid)
            return outcome

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
            outcome = await self._handle_failure(
                record, stop_reason="threshold_expired"
            )
            self._emit_renew_digest(
                record, outcome, ttl_before_ms=ttl_ms, run_uuid=run_uuid
            )
            return outcome

        # ---- Step 3(f): remaining > 24h — cannot renew (API constraint) ----
        if remaining_hours > 24:
            expiration_dt = naive_cst_fromtimestamp(ttl_ms / 1000.0)
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
            self._emit_renew_digest(
                record, "skipped", ttl_before_ms=ttl_ms, run_uuid=run_uuid
            )
            return "skipped"

        # ---- Step 3(g): derived threshold < remaining <= 24h — not yet due ----
        if remaining_hours * 60.0 > self._config.renew_threshold_minutes:
            expiration_dt = naive_cst_fromtimestamp(ttl_ms / 1000.0)
            next_renew = expiration_dt - self._renewal_window
            self._schedule_repo.postpone_renewal(
                self._config.env,
                record["source_table"],
                record["source_id"],
                next_renew,
            )
            log.info(
                "[DeadlineRenewalScheduler] sandbox_id=%s remaining=%.1fh "
                "in (%.0f, %d]min — postponed to %s",
                sandbox_id,
                remaining_hours,
                self._config.renew_threshold_minutes,
                24 * 60,
                next_renew.isoformat(),
            )
            self._emit_renew_digest(
                record, "skipped", ttl_before_ms=ttl_ms, run_uuid=run_uuid
            )
            return "skipped"

        # ---- Step 3(h): 0 <= remaining <= derived threshold — renewal window ----
        # TTL period comes from the configured default_ttl_minutes (1440:
        # identical to the former 86400-second constant); the safety margin
        # is subtracted so an extension never lands exactly on the expiry.
        # WR-03: clamps the requested minutes to a 1-minute floor so
        # extend_ttl never receives a non-positive value — the clamp lives
        # in the module-level _requested_ttl_minutes helper so it keeps
        # honest unit coverage even though the derived threshold (EG-4)
        # makes the negative input unreachable via _renew_one.
        ttl_minutes = _requested_ttl_minutes(
            self._config.default_ttl_minutes,
            remaining_hours,
            self._config.ttl_safety_margin_minutes,
        )

        try:
            renewed = await self._paas_facade.extend_ttl(sandbox_id, ttl_minutes)
        except Exception as e:
            log.warning(
                "[DeadlineRenewalScheduler] extend_ttl failed sandbox_id=%s "
                "ttl_minutes=%d: %s",
                sandbox_id,
                ttl_minutes,
                e,
            )
            log.debug(
                "[DeadlineRenewalScheduler] extend_ttl failed sandbox_id=%s "
                "ttl_minutes=%d: %s",
                sandbox_id,
                ttl_minutes,
                e,
                exc_info=True,
            )
            outcome = await self._handle_failure(
                record,
                stop_reason=("threshold_gone" if _is_confirmed_gone(e) else None),
            )
            self._emit_renew_digest(
                record, outcome, ttl_before_ms=ttl_ms, run_uuid=run_uuid
            )
            return outcome

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
            outcome = await self._handle_failure(record)
            self._emit_renew_digest(
                record, outcome, ttl_before_ms=ttl_ms, run_uuid=run_uuid
            )
            return outcome

        # Renewal success — WR-03: derive next_renew from the authoritative
        # post-extend TTL instead of assuming now + renewal_window. Arca
        # clamps the remaining TTL at its 24h cap
        # (ArcaPaasService._update_device_ttl_sync targets now+23h for that
        # reason), so for configs whose window exceeds the post-extend
        # remaining (default_ttl_minutes > ~2x the cap) the assumed target
        # can land at/after the real expiry and the device expires before
        # the next due scan. Re-read the platform TTL to get the clamped
        # reality; if that read fails, fall back to a short rescan interval
        # so the next round re-derives from the platform via step (a).
        new_expiration_ms: int | None = None
        try:
            post_extend_info = await self._paas_facade.get_device_info(sandbox_id)
            raw_post_ttl = post_extend_info.ttl_timestamp if post_extend_info else None
            if raw_post_ttl:
                new_expiration_ms = int(float(raw_post_ttl))
        except Exception:
            new_expiration_ms = None

        # D1 upper-bound consistency watermark: the platform must not report
        # more remaining life than the pre-extend expiry plus the requested
        # extension minutes (plus tol). An optimistic echo — the platform
        # clamped the TTL but reported the request — exceeds this bound;
        # reject it into the conservative rescan fallback below instead of
        # scheduling past the real expiry. Both values share the platform
        # epoch domain, so the comparison is clock-offset free.
        expected_expiration_ms = ttl_ms + ttl_minutes * 60_000
        if (
            new_expiration_ms is not None
            and new_expiration_ms
            > expected_expiration_ms
            + self._config.post_extend_consistency_tol_minutes * 60_000
        ):
            log.warning(
                "[DeadlineRenewalScheduler] sandbox_id=%s post-extend ttl=%d "
                "exceeds expected=%d + %dmin tol — untrusted, conservative rescan",
                sandbox_id,
                new_expiration_ms,
                expected_expiration_ms,
                self._config.post_extend_consistency_tol_minutes,
            )
            log.info(
                "[arca_ttl_metrics] post_extend_ttl_inconsistent=1 sandbox_id=%s "
                "source_table=%s source_id=%s",
                record.get("sandbox_id"),
                record["source_table"],
                record["source_id"],
            )
            new_expiration_ms = None

        if new_expiration_ms is not None:
            new_expiration_dt = naive_cst_fromtimestamp(new_expiration_ms / 1000)
            # D2: R' stays in the platform epoch domain (same arithmetic as
            # remaining_hours at step 3(c-d)); the persisted next_renew stays
            # a naive Asia/Shanghai wall clock (CR-01).
            # D2 notation R' (plan-level spec) kept as the variable name.
            R_minutes = (new_expiration_ms / 1000.0 - time.time()) / 60.0  # noqa: N806
            window_minutes = self._renewal_window.total_seconds() / 60.0
            cron_minutes = self._config.cron_interval_seconds / 60.0
            if R_minutes > window_minutes:
                next_renew = new_expiration_dt - self._renewal_window
                log.info(
                    "[DeadlineRenewalScheduler] sandbox_id=%s post-extend ttl=%d — "
                    "next_renew derived from clamped expiry",
                    sandbox_id,
                    new_expiration_ms,
                )
            else:
                half_life_minutes = max(R_minutes / 2.0, cron_minutes)
                next_renew = naive_cst_now() + timedelta(minutes=half_life_minutes)
                log.info(
                    "[DeadlineRenewalScheduler] sandbox_id=%s post-extend ttl=%d "
                    "R_minutes=%.1f window_minutes=%.0f — half-life next_renew "
                    "at now + %.1fmin",
                    sandbox_id,
                    new_expiration_ms,
                    R_minutes,
                    window_minutes,
                    half_life_minutes,
                )
        else:
            log.warning(
                "[DeadlineRenewalScheduler] sandbox_id=%s renewed %d min but "
                "post-extend TTL re-read failed — conservative short-interval "
                "rescan; next round re-derives from the platform",
                sandbox_id,
                ttl_minutes,
            )
            next_renew = naive_cst_now() + timedelta(
                seconds=self._config.cron_interval_seconds
            )
        # EG-1 floor: EVERY success outcome schedules at least one full cron
        # interval out — including the status-quo branch, whose E' - window
        # target can land at/behind now when the platform clamps hard (the
        # fallback branch is already at the floor, so max is an identity).
        cron_floor = naive_cst_now() + timedelta(
            seconds=self._config.cron_interval_seconds
        )
        next_renew = max(next_renew, cron_floor)
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
        # h3 digest: success with the pre-renewal platform TTL as before and
        # the clamped post-extend TTL as after (dash placeholder when the
        # re-read failed and new_expiration_ms stayed None).
        self._emit_renew_digest(
            record,
            "success",
            ttl_before_ms=ttl_ms,
            ttl_after_ms=new_expiration_ms,
            run_uuid=run_uuid,
        )
        return "success"

    # ------------------------------------------------------------------
    # Step 4: Failure handling
    # ------------------------------------------------------------------

    async def _handle_failure(
        self, record: dict, *, stop_reason: str | None = None
    ) -> str:
        """Handle renewal failure with a liveness-gated STOPPED threshold.

        Two-verdict model: at the cap, a threaded stop_reason
        (platform-confirmed gone/expired) writes terminal STOPPED with the
        reason; with NO reason nothing terminal is written — the count is
        capped at max_fail_count - 1 (cap-and-hold) and the row retries
        after retry_delay_minutes.

        Args:
            record: The schedule record (dict from list_due_for_renewal).
            stop_reason: Keyword-only verdict threaded from the failure site
                ("threshold_gone" | "threshold_expired"); None means the
                failure was non-confirming.

        Returns:
            "failed" or "stopped".
        """
        new_fail_count = record.get("renew_fail_count", 0) + 1

        if new_fail_count >= self._config.max_fail_count:
            if stop_reason is not None:
                try:
                    self._schedule_repo.set_status(
                        self._config.env,
                        record["source_table"],
                        record["source_id"],
                        "STOPPED",
                        stop_reason=stop_reason,
                    )
                except Exception:
                    # WR-01 (85-86 deep review): a failed terminal write must
                    # not leak into _process_one's secondary routing, which
                    # would downgrade this platform-confirmed verdict to a
                    # non-confirming cap-and-hold and drop the
                    # stopped_transition signal. The verdict and its metrics
                    # are retained this round; the row stays ACTIVE with the
                    # 9+-signature and the write is re-attempted next round.
                    log.exception(
                        "[DeadlineRenewalScheduler] terminal STOPPED write "
                        "failed source=%s:%s stop_reason=%s — verdict "
                        "retained, write re-attempted next round",
                        record["source_table"],
                        record["source_id"],
                        stop_reason,
                    )
                log.warning(
                    "[DeadlineRenewalScheduler] sandbox_id=%s "
                    "source=%s:%s reached max_fail_count=%d, marked STOPPED "
                    "(stop_reason=%s)",
                    record.get("sandbox_id"),
                    record["source_table"],
                    record["source_id"],
                    self._config.max_fail_count,
                    stop_reason,
                )
                # The persisted STOPPED state is now only written on a
                # platform-confirmed verdict (gone error class or expired
                # TTL). After the phase 85 anti-join fix the discovery scan
                # excludes any cold-table row matching (env, source, sandbox),
                # so confirmed-STOPPED rows cannot be revived on the same
                # sandbox. Revival channels stay the device-side lifecycle
                # register() upsert (restart / destroy+create, baas_device
                # rows only) and the stale-old-sandbox discovery safety net.
                # The binding side (ac_entity_device_binding) has NO
                # lifecycle register() writer — its renewal normally
                # continues via the baas_device row for the same container,
                # and a binding row whose device row also went terminal
                # recovers via a new binding record id (re-bind) or a
                # device-side restart. The durable alarm signal remains this
                # metrics line, not the row status.
                log.info(
                    "[arca_ttl_metrics] stopped_transition=1 sandbox_id=%s "
                    "source_table=%s source_id=%s fail_count=%d stop_reason=%s",
                    record.get("sandbox_id"),
                    record["source_table"],
                    record["source_id"],
                    new_fail_count,
                    stop_reason,
                )
                return "stopped"

            # Cap-and-hold: at/over the cap with a NON-confirming verdict
            # the count is held at max_fail_count - 1 (the DB keeps the
            # 9+-signature clue) and the row retries after
            # retry_delay_minutes.
            next_retry = naive_cst_now() + timedelta(
                minutes=self._config.retry_delay_minutes
            )
            self._schedule_repo.update_after_failure(
                self._config.env,
                record["source_table"],
                record["source_id"],
                next_renew_at=next_retry,
                new_fail_count=self._config.max_fail_count - 1,
            )
            log.warning(
                "[DeadlineRenewalScheduler] sandbox_id=%s fail_count held at "
                "%d/%d (non-confirming failure at cap) — retry at %s",
                record.get("sandbox_id"),
                self._config.max_fail_count - 1,
                self._config.max_fail_count,
                next_retry.isoformat(),
            )
            return "failed"

        # Retry: schedule next attempt after retry_delay_minutes
        next_retry = naive_cst_now() + timedelta(
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
            "renew_failure_rate=%.3f due_count=%d gap_detected=%d "
            "suppressed_terminal_count=%d",
            int(time.time()),
            remaining_val,
            failure_rate,
            report.due_count,
            1 if report.gap_detected else 0,
            report.suppressed_terminal_count,
        )
