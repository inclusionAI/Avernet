"""Whitelist-observation mixin — 抽自 record_process_service(R9 行门禁)。

白名单命中后的三路处理(均不发通知):
  - 路 1 有活跃单 → scan 兜底转 OBSERVED(close_for_whitelist_hit)
  - 路 2 有现存观察单 → 复用 refresh_snapshot 刷新快照(状态不变、dt_version guard)
  - 路 3 无活跃无观察单 → 新建 OBSERVED 单(open_observed_ticket,不建 notify)

mixin 不定义 ``__init__``;``self._lifecycle_svc`` / ``self._task_repo`` /
``self._audit_repo`` 由组合后的 :class:`GovernanceRecordService` 主类提供。
方法之间仅通过 self 互调,无跨实例依赖。
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
import uuid

from agentclaw.community.core.economy.governance.domain.enums import AuditAction
from agentclaw.community.core.economy.governance.domain.record import GovernanceRecord
from agentclaw.community.core.economy.governance.domain.ticket import (
    GovernanceTicket,
    MutableSnapshot,
)
from agentclaw.community.core.economy.governance.services.record_process_service import (
    RecordProcessResult,
)

if TYPE_CHECKING:
    pass


class WhitelistObservationMixin:
    """白名单观察三路分发 — ``self._lifecycle_svc`` / ``self._task_repo`` /
    ``self._audit_repo`` 由主类(GovernanceRecordService)提供。"""

    # ------------------------------------------------------------------
    # Internal: Whitelist handling (§7.1.4 Step 2)
    # ------------------------------------------------------------------

    def _handle_whitelist_hit(
        self,
        *,
        active_ticket: GovernanceTicket | None,
        worker_key: str,
        owner_id: str,
        bot_id: str,
        record: GovernanceRecord,
        run_id: str,
        dry_run: bool,
        notify_source: str,
    ) -> RecordProcessResult:
        """Process whitelist-hit cases (§7.1.4 Step 2) — 三路分发。

        白名单 bot 命中线下治理数据,按工单现状三路处理(均不发通知):

        - 路 1 有活跃单(open/scheduled/waiting_review)→ scan 兜底转 OBSERVED
          (close_for_whitelist_hit,审计 SCAN_WHITELISTED)。批量加白漏关 / 加白
          与扫描并发竞态的兜底。
        - 路 2 无活跃单但有现存 OBSERVED 单 → 复用 refresh_snapshot 刷新其快照
          (状态不变、dt_version 严格更新 guard、审计 WHITELIST_OBSERVED)。这就是
          '白名单 bot 持续可见最新治理画像'的核心路径。
        - 路 3 无活跃单也无观察单 → 新建一条 OBSERVED 单(open_observed_ticket,
          不建 notify_log,审计 WHITELIST_OBSERVED)。加白动作本身不建单,off-batch
          来数据时才建观察单。
        """
        del notify_source  # 白名单三路不发通知,notify_source 仅对建活跃单有意义
        now = datetime.now()

        # 路 1:有活跃单 → scan 兜底转 OBSERVED(原逻辑,行为不变只是目标改 OBSERVED)
        if active_ticket is not None:
            if not dry_run:
                self._lifecycle_svc.close_for_whitelist_hit(
                    active_ticket.ticket_id, now=now,
                )
                self._audit_repo.add_audit(
                    run_id, bot_id, owner_id,
                    action_taken=AuditAction.SCAN_WHITELISTED,
                    dry_run=0,
                )
            return RecordProcessResult(
                worker_key=worker_key,
                entered_governance_scope=False,
                action="scan_whitelisted",
                reason="whitelist_hit_active_ticket_observed",
                ticket_id=active_ticket.ticket_id,
            )

        # 无活跃单:查现存观察单
        observed_ticket = self._task_repo.find_observed_ticket(worker_key)

        # 路 2:有观察单 → 刷新(持续画像,不发通知)
        if observed_ticket is not None:
            return self._refresh_observed_ticket(
                observed_ticket=observed_ticket,
                record=record,
                worker_key=worker_key,
                owner_id=owner_id,
                bot_id=bot_id,
                run_id=run_id,
                dry_run=dry_run,
            )

        # 路 3:无活跃单无观察单 → 建观察单(不发通知)
        return self._create_observed_ticket(
            record=record,
            worker_key=worker_key,
            owner_id=owner_id,
            bot_id=bot_id,
            run_id=run_id,
            dry_run=dry_run,
        )

    # ------------------------------------------------------------------
    # Internal: Whitelist observation — 刷新观察单(路 2) + 建观察单(路 3)
    # ------------------------------------------------------------------

    def _refresh_observed_ticket(
        self,
        *,
        observed_ticket: GovernanceTicket,
        record: GovernanceRecord,
        worker_key: str,
        owner_id: str,
        bot_id: str,
        run_id: str,
        dry_run: bool,
    ) -> RecordProcessResult:
        """路 2:刷新白名单 bot 的现存观察单快照(状态不变,不发通知)。

        Guard:仅当 incoming ``dt_version`` 严格新于观察单当前 dt_version 才刷新
        (复用活跃单刷新同款 guard,防 stale record 倒刷画像)。stale dt_version
        跳过刷新,仅记审计。
        """
        dt_version = record.dt_version
        existing_dt = observed_ticket.dt_version or ""
        is_stale = bool(dt_version and existing_dt and dt_version <= existing_dt)

        if not dry_run and not is_stale:
            self._lifecycle_svc.refresh_snapshot(
                observed_ticket.ticket_id,
                dt_version=dt_version,
                bot_name=record.bot_name,
                owner_name=record.owner_name,
                triggered_dimensions=record.hit_dimensions,
                hit_dimensions_count=record.hit_dimensions_count,
                severity=record.governance_max_priority,
                estimated_saving_tokens=record.expected_token_saving,
                saving_ratio=record.saving_ratio,
                token_baseline=record.token_baseline,
                task_summary=record.task_summary,
                notification_structured=record.notification_structured,
                analysis_status=record.analysis_status,
                last_seen_at=datetime.now(),
                last_sync_at=datetime.now(),
                last_decision_dt_version=dt_version,
            )

        audit_dry_run = 0 if not dry_run else 1
        if not dry_run:
            self._audit_repo.add_audit(
                run_id, bot_id, owner_id,
                check_result="actionable",
                governance_decision=record.governance_decision,
                hit_dimensions=record.hit_dimensions,
                action_taken=AuditAction.WHITELIST_OBSERVED,
                error_msg=(
                    "stale_dt_version_skipped" if is_stale else None
                ),
                dry_run=audit_dry_run,
            )
        else:
            # dry_run 预览也留痕(对齐路 3 _create_observed_ticket 与既有
            # Step4 stale-skip 分支的 dry_run 审计口径)
            self._audit_repo.add_audit(
                run_id, bot_id, owner_id,
                check_result="actionable",
                governance_decision=record.governance_decision,
                hit_dimensions=record.hit_dimensions,
                action_taken=AuditAction.WHITELIST_OBSERVED,
                error_msg=(
                    "stale_dt_version_skipped" if is_stale else None
                ),
                dry_run=audit_dry_run,
            )

        return RecordProcessResult(
            worker_key=worker_key,
            entered_governance_scope=False,
            action="whitelist_observed",
            reason=(
                "stale_dt_version_skipped" if is_stale
                else "observed_ticket_refreshed"
            ),
            ticket_id=observed_ticket.ticket_id,
        )

    def _create_observed_ticket(
        self,
        *,
        record: GovernanceRecord,
        worker_key: str,
        owner_id: str,
        bot_id: str,
        run_id: str,
        dry_run: bool,
    ) -> RecordProcessResult:
        """路 3:为白名单 bot 新建一条 OBSERVED 工单(不发通知)。

        加白动作本身不建单;off-batch 来数据时,若该 bot 无活跃单也无现存观察单,
        用当前 record 快照建一条观察单承载持续刷新画像。经 open_observed_ticket
        (不建 notify_log、不设 delivery_status)。
        """
        now = datetime.now()
        ticket_id = uuid.uuid4().hex
        owner_id_val = record.owner_id or owner_id

        if dry_run:
            self._audit_repo.add_audit(
                run_id, bot_id, owner_id_val,
                check_result="actionable",
                governance_decision=record.governance_decision,
                hit_dimensions=record.hit_dimensions,
                action_taken=AuditAction.WHITELIST_OBSERVED,
                dry_run=1,
            )
            return RecordProcessResult(
                worker_key=worker_key,
                entered_governance_scope=False,
                action="would_observe",
                reason="whitelist_hit_no_ticket_would_observe",
                ticket_id=ticket_id,
            )

        ticket_model = GovernanceTicket.create(
            ticket_id=ticket_id,
            worker_id=worker_key,
            bot_id=bot_id,
            owner_id=owner_id_val,
            owner_name=record.owner_name,
            bot_name=record.bot_name,
            snapshot=MutableSnapshot(
                dt_version=record.dt_version,
                initial_decision="actionable",
                current_decision="actionable",
                triggered_dimensions=record.hit_dimensions,
                hit_dimensions_count=record.hit_dimensions_count,
                severity=record.governance_max_priority,
                estimated_saving_tokens=record.expected_token_saving,
                saving_ratio=record.saving_ratio,
                token_baseline=record.token_baseline,
                task_summary=record.task_summary,
                notification_structured=record.notification_structured,
                analysis_status=record.analysis_status,
                consecutive_normal_days=0,
                last_decision_dt_version=record.dt_version,
                last_seen_at=now,
                last_sync_at=now,
            ),
        )
        self._lifecycle_svc.open_observed_ticket(ticket=ticket_model)

        self._audit_repo.add_audit(
            run_id, bot_id, owner_id_val,
            check_result="actionable",
            governance_decision=record.governance_decision,
            hit_dimensions=record.hit_dimensions,
            action_taken=AuditAction.WHITELIST_OBSERVED,
            dry_run=0,
        )

        return RecordProcessResult(
            worker_key=worker_key,
            entered_governance_scope=False,
            action="whitelist_observed",
            reason="observed_ticket_created",
            ticket_id=ticket_id,
        )