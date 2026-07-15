"""Dormant bot candidate filtering and scan/decision service.

Exposes:
  ``filter_candidates(session, N, env)`` — Stage-1/2 SQL+in-memory filter.
  ``DormantBotService`` — orchestrates a full scan run with single-signal
      active detection (/alive session check only).

Note: SQLite has no REGEXP, so entity_id digit-check is done in Python,
not in the SQL query.

Single-signal design (2026-06-22, limo):
  OpenAPI invocations land in session records and are reflected in
  alive.last_session_time.  The /alive endpoint is the sole activity
  signal; the openapi-invocation batch call has been removed.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.bot_dormant.baas_client import (
    BaasDormantClient,
)
from agentclaw.community.core.common_config import CommonWhiteListService
from agentclaw.community.core.bot_dormant.datetime_utils import max_datetime, parse_dt
from agentclaw.community.core.bot_dormant.notify_log import commit_notify_log_idempotent
from agentclaw.community.core.bot_dormant.scan_policy import (
    DEFAULT_INACTIVE_THRESHOLD_DAYS,
    DEFAULT_RECYCLE_GRACE_DAYS,
    DormantScanPolicyService,
    resolve_scan_window,
)
from agentclaw.community.core.bot_dormant.sqlite_models import (
    DormantCheckAudit,
    DormantNotifyLog,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.utils.env_utils import get_current_env


if TYPE_CHECKING:
    from agentclaw.community.core.bot_dormant.protocols import BotServiceProtocol
    from agentclaw.community.core.bot_dormant.sqlite_models import DormantExternalInput

# Re-export so existing callers importing from this module continue to work.
from agentclaw.community.core.bot_dormant.candidates import (
    Candidate, filter_candidates, owner_is_protected, partition_by_protected_owner,
)


logger = get_logger()
OWNER_PROTECTION_BUSINESS_CODE = "bot_dormant"
OWNER_PROTECTION_PARAM_CODE = "protected_owner_ids"


# ---------------------------------------------------------------------------
# RunSummary
# ---------------------------------------------------------------------------


@dataclass
class RunSummary:
    """Result summary of a single DormantBotService.process_run() call."""
    scanned: int = 0
    warned: int = 0
    recycled: int = 0
    skipped: int = 0
    errors: int = 0


# ---------------------------------------------------------------------------
# DormantBotService
# ---------------------------------------------------------------------------


class RecycleReleaseFailed(RuntimeError):
    """stop_bot returned False — the bot's device was not cleanly released
    so we refuse to advance the status to RECYCLED. The caller (process_run
    outer try/except) records an 'error' audit row and the next cron run
    will retry."""


class DormantBotService:
    """Orchestrates a dormant-bot scan+decision run.

    Dependencies are constructor-injected so the caller (DI module) can
    provide real or mock implementations.

    Activity is judged by a single signal: /alive (check_alive).
    OpenAPI invocations land in session records and are reflected in
    alive.last_session_time (limo, 2026-06-22).

    Args:
        db: DatabasePlugin — a fresh ORM session is opened per process_run call
            via ``db.orm_session()``, so this singleton is safe for long-lived
            cron use without holding a connection between runs.
        baas_client: Async BaaS health-checker client (/alive only).
        bot_service: BotServiceProtocol for stop_bot / update_status calls.
        N: Dormant threshold (days). Bots inactive >= N days enter the warn window.
        M: Recycle margin (days). Bots inactive >= N+M days are recycled.
        passport_plugin: Optional passport plugin for credential freeze calls.
    """

    @inject
    def __init__(
        self,
        db: DatabasePlugin,
        baas_client: BaasDormantClient,
        bot_service: BotServiceProtocol,
        passport_plugin: PassportPlugin,
        scan_policy: DormantScanPolicyService,
        common_whitelist_service: CommonWhiteListService,
        N: int = DEFAULT_INACTIVE_THRESHOLD_DAYS,
        M: int = DEFAULT_RECYCLE_GRACE_DAYS,
        dry_run: bool | None = None,
        action_link_pattern: str = "",
    ) -> None:
        """Construct DormantBotService.

        dry_run None (test default) → module-level env helper. action_link_pattern
        is the bot-detail link template (DormantNotifyConfig); "" ⇒ no link.
        """
        self._db = db
        self._baas_client = baas_client
        self._bot_service = bot_service
        self._default_N = N
        self._default_M = M
        self._N = N
        self._M = M
        self._passport_plugin = passport_plugin
        self._dry_run_override = dry_run
        self._scan_policy = scan_policy
        self._common_whitelist_service = common_whitelist_service
        self._action_link_pattern = action_link_pattern

    # ------------------------------------------------------------------
    # Environment helpers
    # ------------------------------------------------------------------

    def is_dry_run(self) -> bool:
        """Return the active dry-run flag.

        Wired in production by DI (DormantScanPolicyService from ac_common_config).
        Falls back to module-level env helper when no override was injected
        (this keeps existing unit tests that don't pass dry_run working).
        """
        if self._dry_run_override is not None:
            return self._dry_run_override
        return self._scan_policy.dry_run()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_audit(
        self,
        session,
        *,
        run_id: str,
        bot_id: str,
        check_result: str,
        action_taken: str,
        owner_id: str | None = None,
        days_inactive: int | None = None,
        dry_run: bool = False,
        error_msg: str | None = None,
        source: str = "dormant_bot_service",
    ) -> None:
        row = DormantCheckAudit(
            run_id=run_id,
            bot_id=bot_id,
            owner_id=owner_id,
            check_result=check_result,
            action_taken=action_taken,
            days_inactive=days_inactive,
            source=source,
            error_msg=error_msg,
            dry_run=1 if dry_run else 0,
        )
        session.add(row)
        session.commit()
        logger.info(
            "[dormant.run=%s] event=audit_written audit_id=%s bot_id=%s "
            "owner_id=%s check_result=%s action_taken=%s source=%s dry_run=%s",
            run_id, row.id, bot_id, owner_id, check_result, action_taken,
            source, dry_run,
        )

    def _first_warn_dt(
        self,
        session,
        *,
        bot_id: str,
        owner_id: str,
        last_active: datetime,
    ) -> datetime | None:
        """Return the earliest warn_enqueued audit gmt_create for this bot,
        bounded to the current governance segment.

        Filters:
        - action_taken='warn_enqueued' — only warn records count
        - source='dormant_bot_service' — internal_scan only; external links
          have their own cooldown semantics (see spec)
        - dry_run=0 — dry_run runs are dress rehearsals; the real-mode cooldown
          must start from the first real warn
        - gmt_create > last_active — a user-return resets governance; only warns
          after the last activity timestamp count

        Returns None if no qualifying warn exists (→ caller should enqueue
        a fresh warn this run).
        """
        from agentclaw.community.core.bot_dormant.sqlite_models import DormantCheckAudit
        return (
            session.query(func.min(DormantCheckAudit.gmt_create))
            .filter(
                DormantCheckAudit.bot_id == bot_id,
                DormantCheckAudit.owner_id == owner_id,
                DormantCheckAudit.action_taken == "warn_enqueued",
                DormantCheckAudit.source == "dormant_bot_service",
                DormantCheckAudit.dry_run == 0,
                DormantCheckAudit.gmt_create > last_active,
            )
            .scalar()
        )

    def _enqueue_warn(
        self,
        session,
        candidate: Candidate,
        days_inactive: int,
        cooldown_days: int,
        dry_run: bool,
    ) -> None:
        """Insert a DormantNotifyLog row with notify_type='warn' and status='pending'.

        content 字段渲染完整模板（含动态 remaining_days）, bot 容器侧拉到直接发,
        不再二次加工。

        cooldown_days = today - first_warn_dt (0 for the first warn of a
        governance segment, up to M-1 for the last warn before recycle).
        remaining_days = M - cooldown_days is computed inside the template.
        """
        from agentclaw.community.core.bot_dormant.templates import render_warn

        dt_str = date.today().strftime("%Y%m%d")
        content = render_warn(
            bot_name=candidate.bot_name or candidate.bot_id,
            days_inactive=days_inactive,
            cooldown_days=cooldown_days,
            M=self._M,
            bot_id=candidate.bot_id,
            action_link_pattern=self._action_link_pattern,
        )
        log_row = DormantNotifyLog(
            bot_id=candidate.bot_id,
            owner_id=candidate.owner_id,
            entity_id=candidate.entity_id,
            notify_type="warn",
            notify_target=candidate.owner_id,
            notify_source="internal_scan",
            content=content,
            dt=dt_str,
            send_status="pending",
            dry_run=1 if dry_run else 0,
        )
        log_row, created = commit_notify_log_idempotent(session, log_row)
        logger.info(
            "[dormant.enqueue] event=%s notify_log_id=%s "
            "notify_type=warn notify_source=internal_scan "
            "bot_id=%s owner_id=%s days=%d cooldown=%d dry_run=%s",
            "notify_enqueued" if created else "notify_already_exists",
            log_row.id if log_row is not None else None,
            candidate.bot_id, candidate.owner_id,
            days_inactive, cooldown_days, dry_run,
        )

    def _enqueue_recycle(
        self, session, candidate: Candidate, days_inactive: int, dry_run: bool
    ) -> None:
        """Insert a DormantNotifyLog row with notify_type='recycle' and status='pending'.

        content 字段渲染完整模板，包含「次日可能再次被回收」提示。
        dry_run 透传到 notify_log 字段，预发联调时也能看到「真实模式下本应发的
        recycle 文案」，但 pending-notifications 默认不会拉到 dry_run=1 行。
        """
        from agentclaw.community.core.bot_dormant.templates import render_recycle

        dt_str = date.today().strftime("%Y%m%d")
        content = render_recycle(
            bot_name=candidate.bot_name or candidate.bot_id,
            bot_id=candidate.bot_id,
            action_link_pattern=self._action_link_pattern,
        )
        log_row = DormantNotifyLog(
            bot_id=candidate.bot_id,
            owner_id=candidate.owner_id,
            entity_id=candidate.owner_id,
            notify_type="recycle",
            notify_target=candidate.owner_id,
            notify_source="internal_scan",
            content=content,
            dt=dt_str,
            send_status="pending",
            dry_run=1 if dry_run else 0,
        )
        log_row, created = commit_notify_log_idempotent(session, log_row)
        logger.info(
            "[dormant.enqueue] event=%s notify_log_id=%s "
            "notify_type=recycle notify_source=internal_scan "
            "bot_id=%s owner_id=%s days=%d dry_run=%s",
            "notify_enqueued" if created else "notify_already_exists",
            log_row.id if log_row is not None else None,
            candidate.bot_id, candidate.owner_id,
            days_inactive, dry_run,
        )

    def _execute_recycle(self, candidate: Candidate, dry_run: bool) -> None:
        """Recycle a single bot.

        Sequence (NOT a DB transaction — the backend uses autocommit and
        the calls fan out to BaaS / license service):

        1. stop_bot
           - returns True if both DB row reset AND device released cleanly
           - returns False if status was reset but device-release failed.
             False means resources may still be charged — we must not pretend
             the bot is RECYCLED. Raise so the caller writes an 'error'
             audit row and skips this bot. Next cron run will retry.
           - raises on hard errors (e.g. BotServiceError) — also bubbles up.
        2. update_status='RECYCLED'
           - only reached when stop_bot succeeded. If THIS raises, the bot
             is left at PENDING (set by stop_bot) with no binding/device.
             That state is benign — bot is effectively unusable, next cron
             will pick it up and retry.
        3. passport.freeze_agent_passport (best-effort)
           - failures are logged but do NOT raise: the device is already
             released; not freezing the license is at worst a billing
             leak the cron will retry via re-classification next day.
        """
        if dry_run:
            logger.info(
                "[DormantBotService._execute_recycle] dry_run=True, skipping recycle for bot_id=%s",
                candidate.bot_id,
            )
            return

        release_ok = self._bot_service.stop_bot(
            bot_id=candidate.bot_id,
            user_id=candidate.owner_id,
            release_reason="dormant_recycle",
        )
        # bot_service.stop_bot returns False when the device release failed
        # but status was reset to PENDING; treat as a recoverable error so
        # the candidate is recorded as 'error' and we retry tomorrow.
        if release_ok is False:
            raise RecycleReleaseFailed(
                f"stop_bot reported release failure for bot_id={candidate.bot_id}; "
                "device may still hold resources, refusing to mark RECYCLED"
            )

        self._bot_service.update_status(
            bot_id=candidate.bot_id, user_id=candidate.owner_id, status="RECYCLED"
        )

        try:
            self._passport_plugin.freeze_agent_passport(
                bot_id=candidate.bot_id,
                owner_workno=candidate.owner_id,
                reason="dormant recycle",
            )
        except Exception as exc:
            logger.warning(
                "[DormantBotService._execute_recycle] passport freeze failed "
                "bot_id=%s: %s — continuing (recycle considered complete)",
                candidate.bot_id,
                exc,
            )

    def _enqueue_external_warn(
        self, session, row: DormantExternalInput, bot_name: str, owner_id: str,
        dry_run: bool,
    ) -> None:
        """Enqueue a warn notification for an external_input row.

        Content priority: row.notify_content (verbatim) → render_external_fallback.
        dry_run 透传到 notify_log，让 pending-notifications 默认不会拉到预发数据。
        """
        from agentclaw.community.core.bot_dormant.templates import render_external_fallback

        dt_str = date.today().strftime("%Y%m%d")
        content = row.notify_content or render_external_fallback(
            bot_name=bot_name or row.bot_id,
            governance_source=row.governance_source,
            governance_dimension=row.governance_dimension,
            reason=row.reason,
            bot_id=row.bot_id,
            action_link_pattern=self._action_link_pattern,
        )
        log_row = DormantNotifyLog(
            bot_id=row.bot_id,
            owner_id=owner_id,
            entity_id=None,
            notify_type="warn",
            notify_target=owner_id,
            notify_source="external_input",
            content=content,
            dt=dt_str,
            send_status="pending",
            dry_run=1 if dry_run else 0,
        )
        log_row, created = commit_notify_log_idempotent(session, log_row)
        logger.info(
            "[dormant.enqueue] event=%s notify_log_id=%s "
            "notify_type=warn notify_source=external_input "
            "bot_id=%s owner_id=%s dry_run=%s",
            "notify_enqueued" if created else "notify_already_exists",
            log_row.id if log_row is not None else None,
            row.bot_id, owner_id, dry_run,
        )

    def _enqueue_external_recycle(
        self, session, row: DormantExternalInput, bot_name: str, owner_id: str,
        dry_run: bool,
    ) -> None:
        """Enqueue the final recycle notification for an external_input row."""
        from agentclaw.community.core.bot_dormant.templates import render_external_fallback

        dt_str = date.today().strftime("%Y%m%d")
        content = row.notify_content or render_external_fallback(
            bot_name=bot_name or row.bot_id,
            governance_source=row.governance_source,
            governance_dimension=row.governance_dimension,
            reason=row.reason,
            bot_id=row.bot_id,
            action_link_pattern=self._action_link_pattern,
        )
        log_row = DormantNotifyLog(
            bot_id=row.bot_id,
            owner_id=owner_id,
            entity_id=None,
            notify_type="recycle",
            notify_target=owner_id,
            notify_source="external_input",
            content=content,
            dt=dt_str,
            send_status="pending",
            dry_run=1 if dry_run else 0,
        )
        log_row, created = commit_notify_log_idempotent(session, log_row)
        logger.info(
            "[dormant.enqueue] event=%s notify_log_id=%s "
            "notify_type=recycle notify_source=external_input "
            "bot_id=%s owner_id=%s dry_run=%s",
            "notify_enqueued" if created else "notify_already_exists",
            log_row.id if log_row is not None else None,
            row.bot_id, owner_id, dry_run,
        )

    def _process_external_inputs(self, session, dry_run: bool, summary: RunSummary,
                                 run_id: str, protected_owner_ids: frozenset[str]) -> None:
        """Two-stage governance for external_input rows.

        Each row triggers warns daily until days_since_input >= M, then a final
        recycle. Per-row try/except isolates failures.

        Audit: every branch (whitelist skip / missing bot / invalid dt /
        future date / warn / recycle / unexpected exception) writes a
        DormantCheckAudit row with source='external_input' so the run is
        fully observable in the audit table.
        """
        from datetime import date as _date
        from datetime import datetime as _datetime

        from agentclaw.community.core.bot_dormant.sqlite_models import (
            DormantExternalInput,
            DormantWhitelist,
        )

        # Whitelist (priority over external_input).
        # Key by (bot_id, owner_id) — bot_id='default' is per-owner, so
        # a bot_id alone is ambiguous.
        whitelist = {
            (wb.bot_id, wb.owner_id)
            for wb in session.query(DormantWhitelist).all()
        }

        rows = (
            session.query(DormantExternalInput)
            .filter(DormantExternalInput.processed == 0)
            .all()
        )
        logger.info(
            "[dormant.run=%s] event=external_sweep_start rows=%d whitelist_size=%d",
            run_id, len(rows), len(whitelist),
        )

        today = _date.today()
        for row in rows:
            try:
                owner_protected = owner_is_protected(row.owner_id, protected_owner_ids)
                is_bot_whitelisted = (row.bot_id, row.owner_id) in whitelist
                if owner_protected or is_bot_whitelisted:
                    reason = (
                        "protected_owner" if owner_protected else "whitelisted"
                    )
                    logger.info(
                        "[dormant.run=%s] event=external_skip reason=%s "
                        "bot_id=%s owner_id=%s",
                        run_id, reason, row.bot_id, row.owner_id,
                    )
                    self._write_audit(
                        session,
                        run_id=run_id,
                        bot_id=row.bot_id,
                        owner_id=row.owner_id,
                        check_result="whitelisted",
                        action_taken="skipped",
                        dry_run=dry_run,
                        source="external_input",
                    )
                    continue

                bot: BotModel | None = (
                    session.query(BotModel)
                    .filter(
                        BotModel.bot_id == row.bot_id,
                        BotModel.owner_id == row.owner_id,
                    )
                    .first()
                )
                if bot is None:
                    logger.warning(
                        "[dormant.run=%s] event=external_skip reason=missing_bot "
                        "bot_id=%s owner_id=%s",
                        run_id, row.bot_id, row.owner_id,
                    )
                    self._write_audit(
                        session,
                        run_id=run_id,
                        bot_id=row.bot_id,
                        owner_id=row.owner_id,
                        check_result="missing_bot",
                        action_taken="skipped",
                        dry_run=dry_run,
                        source="external_input",
                    )
                    continue

                # dt is 'YYYYMMDD' string
                try:
                    input_date = _datetime.strptime(row.dt, "%Y%m%d").date()
                except (ValueError, TypeError):
                    logger.warning(
                        "[dormant.run=%s] event=external_skip reason=invalid_dt "
                        "row_id=%s bot_id=%s dt=%r",
                        run_id, row.id, row.bot_id, row.dt,
                    )
                    self._write_audit(
                        session,
                        run_id=run_id,
                        bot_id=row.bot_id,
                        owner_id=row.owner_id,
                        check_result="error",
                        action_taken="skipped",
                        dry_run=dry_run,
                        error_msg=f"invalid dt={row.dt!r}",
                        source="external_input",
                    )
                    continue

                days_since_input = (today - input_date).days
                if days_since_input < 0:
                    # Future-dated row (clock skew or bad input) — leave alone
                    logger.warning(
                        "[dormant.run=%s] event=external_skip reason=future_dated "
                        "bot_id=%s owner_id=%s dt=%s",
                        run_id, row.bot_id, row.owner_id, row.dt,
                    )
                    self._write_audit(
                        session,
                        run_id=run_id,
                        bot_id=row.bot_id,
                        owner_id=row.owner_id,
                        check_result="error",
                        action_taken="skipped",
                        dry_run=dry_run,
                        error_msg=f"future-dated dt={row.dt}",
                        source="external_input",
                    )
                    continue

                if days_since_input < self._M:
                    # Warn phase (today is day 0..M-1)
                    logger.info(
                        "[dormant.run=%s] event=external_warn "
                        "bot_id=%s owner_id=%s days_since_input=%d M=%d source=%s",
                        run_id, row.bot_id, row.owner_id,
                        days_since_input, self._M, row.governance_source,
                    )
                    self._enqueue_external_warn(
                        session, row, bot.bot_name or bot.bot_id, bot.owner_id, dry_run
                    )
                    self._write_audit(
                        session,
                        run_id=run_id,
                        bot_id=row.bot_id,
                        owner_id=row.owner_id,
                        check_result="inactive",
                        action_taken="warn_enqueued",
                        days_inactive=days_since_input,
                        dry_run=dry_run,
                        source="external_input",
                    )
                    summary.warned += 1
                    continue

                # days_since_input >= M → recycle
                # Always enqueue notify_log (dry_run flag carried) so predict-only
                # runs can still surface the would-be recycle message; only real
                # side-effects (stop_bot / mark processed) gated on dry_run.
                logger.info(
                    "[dormant.run=%s] event=external_recycle "
                    "bot_id=%s owner_id=%s days_since_input=%d M=%d "
                    "dry_run=%s source=%s",
                    run_id, row.bot_id, row.owner_id,
                    days_since_input, self._M, dry_run, row.governance_source,
                )
                self._enqueue_external_recycle(
                    session, row, bot.bot_name or bot.bot_id, bot.owner_id, dry_run
                )
                if not dry_run:
                    candidate = Candidate(
                        bot_id=bot.bot_id,
                        entity_id=bot.entity_id,
                        owner_id=bot.owner_id,
                        bot_name=bot.bot_name,
                        gmt_create=bot.gmt_create,
                    )
                    self._execute_recycle(candidate, dry_run=False)
                    # mark processed only after stop_bot returned cleanly
                    row.processed = 1
                    session.commit()
                self._write_audit(
                    session,
                    run_id=run_id,
                    bot_id=row.bot_id,
                    owner_id=row.owner_id,
                    check_result="inactive",
                    action_taken="recycled",
                    days_inactive=days_since_input,
                    dry_run=dry_run,
                    source="external_input",
                )
                summary.recycled += 1
            except Exception as exc:
                # Roll back so the session can keep going; without this any
                # subsequent commit (audit / other rows) would raise
                # PendingRollbackError and abort the whole run.
                session.rollback()
                summary.errors += 1
                logger.exception(
                    "[dormant.run=%s] event=external_error row_id=%s bot_id=%s",
                    run_id, row.id, row.bot_id,
                )
                # Best-effort audit so the failure is observable. Wrap in
                # try/except — if audit itself fails (e.g. DB blew up), the
                # outer loop must keep moving.
                try:
                    self._write_audit(
                        session,
                        run_id=run_id,
                        bot_id=row.bot_id,
                        owner_id=row.owner_id,
                        check_result="error",
                        action_taken="skipped",
                        dry_run=dry_run,
                        error_msg=str(exc)[:512],
                        source="external_input",
                    )
                except Exception:
                    logger.exception(
                        "[dormant.run=%s] event=audit_write_failed row_id=%s bot_id=%s",
                        run_id, row.id, row.bot_id,
                    )

        logger.info(
            "[dormant.run=%s] event=external_sweep_end warned_so_far=%d "
            "recycled_so_far=%d errors_so_far=%d",
            run_id, summary.warned, summary.recycled, summary.errors,
        )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def process_run(
        self, dry_run: bool, run_id: str | None = None,
    ) -> RunSummary:
        """Run a full dormant-bot scan+decision cycle.

        Opens a fresh ORM session for the duration of this run; the session is
        closed (and the connection returned to the pool) before returning, making
        the service safe for long-lived cron use.

        Steps:
        1. filter_candidates(N, env) to get eligible bots.
        2. For each candidate: check_alive (N+M window) — single-signal /alive.
           unknown → skip; alive.result=='true' → active/none;
           else days_inactive from max(alive.last_session_time, gmt_create):
             < N         → active/none
             [N, N+M)    → warn enqueue
             >= N+M      → recycle (dry_run skips stop_bot)

        /alive is the sole activity signal.  OpenAPI invocations land in session
        records and are reflected in alive.last_session_time (limo, 2026-06-22).

        Args:
            dry_run: When True, recycle actions (stop_bot, update_status,
                     passport.freeze_agent_passport) are skipped. Audit rows are still written
                     with dry_run=1.
            run_id:  Optional caller-supplied UUID for the run, so a fire-and-forget
                     caller can hand it back to the user immediately (used by
                     the /trigger-scan endpoint to avoid Tengine 60s timeout).
                     None → service generates one as before.

        Returns:
            RunSummary with counts of scanned/warned/recycled/skipped/errors.
        """
        with self._db.orm_session() as session:
            return await self._process_run_inner(session, dry_run, run_id=run_id)

    async def _process_run_inner(
        self, session, dry_run: bool, run_id: str | None = None,
    ) -> RunSummary:
        """Inner implementation of process_run with an already-open session."""
        if run_id is None:
            run_id = str(uuid.uuid4())
        summary = RunSummary()
        self._N, self._M = resolve_scan_window(
            self._scan_policy,
            default_inactive_threshold_days=self._default_N,
            default_recycle_grace_days=self._default_M,
        )
        env = get_current_env()
        protected_owner_ids = self._common_whitelist_service.get_owner_ids(
            business_code=OWNER_PROTECTION_BUSINESS_CODE,
            param_code=OWNER_PROTECTION_PARAM_CODE, env=env,
        )
        logger.info("[dormant.run=%s] event=protected_owners_loaded env=%s owner_count=%d",
                    run_id, env, len(protected_owner_ids))

        logger.info(
            "[dormant.run=%s] event=run_start dry_run=%s N=%d M=%d",
            run_id, dry_run, self._N, self._M,
        )

        # Step 1: gather candidates
        candidates = filter_candidates(session, self._N, env)
        protected_candidates, candidates = partition_by_protected_owner(
            candidates, protected_owner_ids
        )
        summary.scanned = len(candidates)
        logger.info("[dormant.run=%s] event=protected_owners_filtered skipped=%d sample=%s",
                    run_id, len(protected_candidates),
                    [f"{c.bot_id}@{c.owner_id}" for c in protected_candidates[:5]])

        # Sample a few candidate bot_ids so operators can verify filtering
        # without dumping the whole list.
        sample_ids = [
            f"{c.bot_id}@{c.owner_id}" for c in candidates[:5]
        ]
        logger.info(
            "[dormant.run=%s] event=candidates_filtered scanned=%d sample=%s",
            run_id, len(candidates), sample_ids,
        )

        if not candidates:
            logger.info(
                "[dormant.run=%s] event=internal_loop_skipped reason=no_candidates",
                run_id,
            )

        now = datetime.now(UTC).replace(tzinfo=None)
        window_minutes = (self._N + self._M) * 1440

        # Step 2: per-candidate decision via /alive (single signal).
        #
        # Each candidate is processed in isolation: a single bot's exception
        # (DB / BotService / external call) must NEVER abort the whole run.
        # The catch-all writes an 'error' audit row and increments
        # summary.errors so operators can see the failure surface; the loop
        # continues with the next candidate.
        for c in candidates:
            try:
                await self._process_one_candidate(
                    session, c, now, window_minutes, run_id, dry_run, summary
                )
            except Exception as exc:
                # Roll back so the session can keep going; without this any
                # subsequent commit (audit / next candidate) would raise
                # PendingRollbackError and abort the whole run.
                session.rollback()
                summary.errors += 1
                logger.exception(
                    "[DormantBotService.process_run] candidate failed "
                    "run_id=%s bot_id=%s — continuing with next",
                    run_id,
                    c.bot_id,
                )
                # Best-effort audit so the failure is observable. If THIS
                # itself fails, swallow — the run must keep moving.
                try:
                    self._write_audit(
                        session,
                        run_id=run_id,
                        bot_id=c.bot_id,
                        owner_id=c.owner_id,
                        check_result="error",
                        action_taken="skipped",
                        dry_run=dry_run,
                        error_msg=str(exc)[:512],
                    )
                except Exception:
                    logger.exception(
                        "[DormantBotService.process_run] audit write failed "
                        "run_id=%s bot_id=%s",
                        run_id,
                        c.bot_id,
                    )

        # After internal-scan loop completes, also process external_input rows.
        try:
            self._process_external_inputs(
                session, dry_run, summary, run_id, protected_owner_ids
            )
        except Exception:
            logger.exception(
                "[DormantBotService.process_run] external_inputs sweep failed — "
                "continuing with partial results"
            )

        logger.info(
            "[dormant.run=%s] event=run_end dry_run=%s "
            "scanned=%d warned=%d recycled=%d skipped=%d errors=%d",
            run_id, dry_run,
            summary.scanned, summary.warned, summary.recycled,
            summary.skipped, summary.errors,
        )
        return summary

    async def _process_one_candidate(
        self,
        session,
        c: Candidate,
        now: datetime,
        window_minutes: int,
        run_id: str,
        dry_run: bool,
        summary: RunSummary,
    ) -> None:
        """Decide and act on a single candidate.

        Caller wraps in try/except — this method may raise on transient
        downstream failures (BaaS, BotService, DB); the caller will record
        an 'error' audit row and move on.
        """
        alive = await self._baas_client.check_alive(
            bot_id=c.bot_id,
            entity_id=c.entity_id,
            minutes=window_minutes,
        )
        logger.info(
            "[dormant.run=%s] event=alive_check bot_id=%s owner_id=%s "
            "alive_result=%s last_session_time=%s",
            run_id, c.bot_id, c.owner_id, alive.result, alive.last_session_time,
        )

        # Branch B1: unknown (or any unexpected value) → skip.
        # Only 'true' and 'false' are actionable; treat everything else as
        # unknown so we never wrongly mark a bot inactive on an unexpected
        # signal. The defensive parsing already lives in baas_client; this
        # is a second-line guard for forward-compat (future enum values).
        if alive.result not in ("true", "false"):
            if alive.result != "unknown":
                logger.warning(
                    "[DormantBotService] Unexpected alive.result=%r for bot_id=%s"
                    " — treating as unknown, skipping",
                    alive.result,
                    c.bot_id,
                )
            self._write_audit(
                session,
                run_id=run_id,
                bot_id=c.bot_id,
                owner_id=c.owner_id,
                check_result="unknown",
                action_taken="skipped",
                dry_run=dry_run,
            )
            summary.skipped += 1
            return

        # Branch B3: alive → active
        if alive.result == "true":
            self._write_audit(
                session,
                run_id=run_id,
                bot_id=c.bot_id,
                owner_id=c.owner_id,
                check_result="active",
                action_taken="none",
                dry_run=dry_run,
            )
            return

        # alive.result == 'false' — compute days_inactive
        last_session_dt = parse_dt(alive.last_session_time)
        last_active = max_datetime(last_session_dt, c.gmt_create)
        if last_active is None:
            last_active = c.gmt_create
        days_inactive = (now - last_active).days

        if days_inactive < self._N:
            # 未到沉寂阈值
            self._write_audit(
                session,
                run_id=run_id,
                bot_id=c.bot_id,
                owner_id=c.owner_id,
                check_result="active",
                action_taken="none",
                days_inactive=days_inactive,
                dry_run=dry_run,
            )
            return

        # days_inactive >= N → 治理候选, 看冷静期
        first_warn_dt = self._first_warn_dt(
            session,
            bot_id=c.bot_id,
            owner_id=c.owner_id,
            last_active=last_active,
        )

        if first_warn_dt is None:
            # 本治理段内还未发过 warn → enqueue 第一次 warn
            cooldown_days = 0
            self._enqueue_warn(session, c, days_inactive, cooldown_days, dry_run)
            self._write_audit(
                session,
                run_id=run_id,
                bot_id=c.bot_id,
                owner_id=c.owner_id,
                check_result="inactive",
                action_taken="warn_enqueued",
                days_inactive=days_inactive,
                dry_run=dry_run,
            )
            summary.warned += 1
            return

        cooldown_days = (now - first_warn_dt).days
        if cooldown_days < self._M:
            # 冷静期未到 → 继续 warn(每天一条, 文案 remaining_days = M - cooldown)
            self._enqueue_warn(session, c, days_inactive, cooldown_days, dry_run)
            self._write_audit(
                session,
                run_id=run_id,
                bot_id=c.bot_id,
                owner_id=c.owner_id,
                check_result="inactive",
                action_taken="warn_enqueued",
                days_inactive=days_inactive,
                dry_run=dry_run,
            )
            summary.warned += 1
            return

        # cooldown_days >= M → 冷静期到了, 正式 recycle
        self._enqueue_recycle(session, c, days_inactive, dry_run)
        self._execute_recycle(c, dry_run)
        self._write_audit(
            session,
            run_id=run_id,
            bot_id=c.bot_id,
            owner_id=c.owner_id,
            check_result="inactive",
            action_taken="recycled",
            days_inactive=days_inactive,
            dry_run=dry_run,
        )
        summary.recycled += 1
