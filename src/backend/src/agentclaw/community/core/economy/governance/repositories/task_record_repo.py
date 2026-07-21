"""TaskRecord repository — ``ac_governance_task_record_daily`` ticket lifecycle.

方案 A:本 repo **退化为加载-持久化原语**,不含状态机推进。查询方法
(find_/list_/count_)继承自 :class:`TaskRecordQueryMixin`;本类保留
``__init__``、持久化原语(``save_ticket`` / ``_save_ticket_with_snapshot`` /
``add_ticket``)、唯一豁免的批量原语 ``bulk_close_open``(SQL WHERE 守卫)、
admin delete/count、test seeding。**状态机推进全部上移** 到
:class:`GovernanceLifecycleService`(find→领域守卫→save),入口服务(三渠道)
只调驱动服务、不再调本 repo 语义 command —— 方案 A"唯一驱动者"由分层保证。
本 repo 残留的 9 个语义 command 已在 Task 9 删除(双 grep 守卫锁住)。

Follows the ``DatabasePlugin`` self-managed session pattern
(see ``harness_patch_record_repository``): each method opens its
own ``orm_session()`` — self-managed sessions; env is resolved
internally via ``get_current_env()``.
"""
from __future__ import annotations

from datetime import datetime

from agentclaw.community.core.economy.governance.domain.enums import GovernanceStatus
from agentclaw.community.core.economy.governance.domain.ticket import GovernanceTicket
from agentclaw.community.core.economy.governance.repositories.orm import GovernanceTicketOrm
from agentclaw.community.core.economy.governance.repositories.task_record_query import (
    TaskRecordQueryMixin,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env
from injector import inject
from sqlalchemy import func


log = get_logger(__name__)


def _extract_owner_id(worker_id: str) -> str:
    """Extract owner_id from ``worker_id`` ('{owner_id}:{bot_id}').

    Follows the established convention: single split on first colon.
    """
    return worker_id.split(":", 1)[0]


class TaskRecordRepository(TaskRecordQueryMixin):
    """Ticket lifecycle access to ``ac_governance_task_record_daily``.

    查询方法(find_/list_/count_)继承自 :class:`TaskRecordQueryMixin`;
    本类保留 __init__、状态机命令、admin 方法。
    """

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Ticket mutations (write / lifecycle)
    # ------------------------------------------------------------------

    def add_ticket(
        self,
        *,
        ticket_id: str,
        worker_id: str,
        assignee: str,
        bot_id: str,
        owner_id: str,
        dt_version: str,
        initial_decision: str = "actionable",
        current_decision: str = "actionable",
        triggered_dimensions: str | None = None,
        hit_dimensions_count: int | None = None,
        severity: str | None = None,
        estimated_saving_tokens: int | None = None,
        saving_ratio: float | None = None,
        bot_name: str | None = None,
        owner_name: str | None = None,
        token_baseline: int | None = None,
        task_summary: str | None = None,
        notification_structured: str | None = None,
        analysis_status: str | None = None,
        governance_status: str = "open",
        consecutive_normal_days: int = 0,
        remind_at: datetime | None = None,
        remind_count: int = 0,
        last_seen_at: datetime | None = None,
        last_sync_at: datetime | None = None,
        last_decision_dt_version: str | None = None,
    ) -> str:
        """Insert a new ticket row (self-managed session).

        Parameter names use domain terminology; internal mapping writes
        the corresponding ORM column names.

        Flush ensures subsequent ``find_active_ticket()`` can see this
        newly created ticket.

        Returns the ticket_id of the inserted row.
        """
        row = GovernanceTicketOrm(
            ticket_id=ticket_id,
            worker_id=worker_id,
            active_worker=assignee,
            bot_id=bot_id,
            owner_id=owner_id,
            owner_name=owner_name,
            dt_version=dt_version,
            governance_decision=initial_decision,
            latest_decision=current_decision,
            hit_dimensions=triggered_dimensions,
            hit_dimensions_count=hit_dimensions_count,
            governance_max_priority=severity,
            expected_token_saving=estimated_saving_tokens,
            saving_ratio=saving_ratio,
            bot_name=bot_name,
            token_baseline=token_baseline,
            task_summary=task_summary,
            notification_structured=notification_structured,
            analysis_status=analysis_status,
            governance_status=governance_status,
            consecutive_normal_days=consecutive_normal_days,
            remind_at=remind_at,
            remind_count=remind_count,
            last_seen_at=last_seen_at,
            last_sync_at=last_sync_at,
            last_decision_dt_version=last_decision_dt_version,
        )
        with self._db.orm_session() as s:
            s.add(row)
            s.flush()
            return row.ticket_id

    def save_ticket(self, ticket: GovernanceTicket) -> bool:
        """Persist a (mutated) domain ticket back to its row (方案 A primitive).

        Loads the existing ORM row by ``ticket_id`` (env-scoped), writes the
        model's mutable lifecycle state back via ``apply_to`` (snapshot and
        sealed columns untouched), and commits. This is the **sole
        persistence primitive** for state-machine transitions driven by
        ``GovernanceLifecycleService`` — repo holds no semantic transition
        command; the caller (driver service) invokes the model's guarded
        state-machine method before calling this.

        ``apply_to`` writes only lifecycle fields (governance_status /
        close_reason / closed_at / remind_at / active_worker / ...), so this
        is correct for state transitions (which do not change the snapshot).
        For snapshot refresh (``refresh_snapshot`` path), the driver uses
        ``to_orm`` instead — see ``_save_ticket_with_snapshot``.

        Returns True if the ticket was found and updated, False if not found.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            db_ticket = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.ticket_id == ticket.ticket_id,
                    GovernanceTicketOrm.env == _env,
                )
                .one_or_none()
            )
            if db_ticket is None:
                return False
            ticket.apply_to(db_ticket)
            try:
                s.commit()
            except Exception:
                s.rollback()
                raise
            return True

    def _save_ticket_with_snapshot(self, ticket: GovernanceTicket) -> bool:
        """Persist a ticket whose **snapshot** changed (方案 A primitive).

        Unlike ``save_ticket`` (which uses ``apply_to`` and skips the
        snapshot), this uses ``to_orm`` to write the full row — snapshot +
        lifecycle + identity. Used by the driver's ``refresh_snapshot`` path
        (offline-batch snapshot refresh is the only transition-class operation
        that mutates the snapshot; ``governance_status`` is unchanged).

        Returns True if the ticket was found and updated, False if not found.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            db_ticket = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.ticket_id == ticket.ticket_id,
                    GovernanceTicketOrm.env == _env,
                )
                .one_or_none()
            )
            if db_ticket is None:
                return False
            ticket.to_orm(db_ticket)
            try:
                s.commit()
            except Exception:
                s.rollback()
                raise
            return True

    def bulk_close_open(
        self,
        *,
        close_reason: str,
        closed_at: datetime,
        cooldown_until: datetime | None = None,
        close_conclusion: str | None = None,
    ) -> int:
        """Bulk-close all active open tickets — admin close_all_open.

        **Bulk primitive — sole exemption from the load→model→apply_to chain**
        (performance: cannot load N domain models for batch UPDATE). State
        legality is enforced by the SQL ``WHERE governance_status IN
        ('open','scheduled')`` predicate (equivalent to the white-list guard).
        Callers are converged to the driver service ``bulk_close_open``, which
        orchestrates audit + notify-cancel around this primitive.

        ``close_conclusion`` 透传批量关单结论(批量场景统一落
        ``AdminCloseConclusion.BULK_CLOSED``),逐行带同一结论值。

        Returns the number of rows affected.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            count = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.governance_status.in_(
                        (GovernanceStatus.OPEN, GovernanceStatus.SCHEDULED),
                    ),
                    GovernanceTicketOrm.active_worker.isnot(None),
                    GovernanceTicketOrm.env == _env,
                )
                .update(
                    {
                        GovernanceTicketOrm.governance_status: GovernanceStatus.CLOSED,
                        GovernanceTicketOrm.close_reason: close_reason,
                        GovernanceTicketOrm.closed_at: closed_at,
                        GovernanceTicketOrm.active_worker: None,
                        **(
                            {GovernanceTicketOrm.close_conclusion: close_conclusion}
                            if close_conclusion is not None
                            else {}
                        ),
                        **(
                            {GovernanceTicketOrm.cooldown_until: cooldown_until}
                            if cooldown_until is not None
                            else {}
                        ),
                    },
                    synchronize_session="fetch",
                )
            )
            try:
                s.commit()
            except Exception:
                s.rollback()
                raise
            return count

    # ------------------------------------------------------------------
    # Admin delete / count (admin delete endpoint, §7.5)
    # ------------------------------------------------------------------

    def count_by_dt_versions(
        self, dt_versions: list[str],
    ) -> dict[str, int]:
        """Count rows per dt_version for the given list (env-scoped)."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(
                    GovernanceTicketOrm.dt_version,
                    func.count(),
                )
                .filter(
                    GovernanceTicketOrm.dt_version.in_(dt_versions),
                    GovernanceTicketOrm.env == _env,
                )
                .group_by(GovernanceTicketOrm.dt_version)
                .all()
            )
            return {r[0]: r[1] for r in rows}

    def delete_by_dt_versions(
        self, dt_versions: list[str],
    ) -> int:
        """Delete rows matching dt_versions (env-scoped). Returns deleted count."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            count = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.dt_version.in_(dt_versions),
                    GovernanceTicketOrm.env == _env,
                )
                .delete(synchronize_session="fetch")
            )
            return count

    def delete_by_ids(
        self, ids: list[int],
    ) -> tuple[int, list[int]]:
        """Delete rows by primary key IDs (env-scoped).

        Returns (deleted_count, not_found_ids).
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            existing = (
                s.query(GovernanceTicketOrm.id)
                .filter(
                    GovernanceTicketOrm.id.in_(ids),
                    GovernanceTicketOrm.env == _env,
                )
                .all()
            )
            existing_ids = {r.id for r in existing}
            not_found = [i for i in ids if i not in existing_ids]

            if existing_ids:
                deleted = (
                    s.query(GovernanceTicketOrm)
                    .filter(GovernanceTicketOrm.id.in_(existing_ids))
                    .delete(synchronize_session="fetch")
                )
            else:
                deleted = 0

            return deleted, not_found

    def count_by_ids(
        self, ids: list[int],
    ) -> tuple[int, list[int]]:
        """Count matching rows by primary key IDs (env-scoped), no deletion.

        Returns (match_count, not_found_ids).
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            existing = (
                s.query(GovernanceTicketOrm.id)
                .filter(
                    GovernanceTicketOrm.id.in_(ids),
                    GovernanceTicketOrm.env == _env,
                )
                .all()
            )
            existing_ids = {r.id for r in existing}
            not_found = [i for i in ids if i not in existing_ids]
            return len(existing_ids), not_found

    def delete_by_ticket_id(self, ticket_id: str) -> int:
        """Delete the single ticket row matching ticket_id (env-scoped).

        Single-SQL delete (`WHERE env=? AND ticket_id=?`). Returns the
        number of rows deleted (0 or 1). Used by the ticket-cascade admin
        endpoint to precisely delete one ticket without write amplification.

        Note: ``find_by_ticket_id`` (existence assertion, returns the
        domain ``GovernanceTicket``) is provided by ``TaskRecordQueryMixin``
        — this method only performs the deletion.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            deleted = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.ticket_id == ticket_id,
                    GovernanceTicketOrm.env == _env,
                )
                .delete(synchronize_session="fetch")
            )
            return deleted

    # ------------------------------------------------------------------
    # Test seeding (self-managed session + commit)
    # ------------------------------------------------------------------

    def insert_ticket(self, row: GovernanceTicketOrm) -> None:
        """Insert a full-featured ticket row (self-managed session).

        Preserves every column (ticket_id, governance_status, active_worker,
        etc.) so endpoint tests can seed realistic states without bypassing
        the repo layer.
        """
        with self._db.orm_session() as session:
            session.add(row)
            session.flush()

    def update_delivery_status(self, ticket_id: str, status: str) -> bool:
        """Update a single ticket's delivery_status (self-managed session).

        Args:
            ticket_id: 工单稳定 UUID。
            status: 投递状态单值(pending/sent/failed/cancelled)。

        Returns:
            True if 1 row updated, False otherwise.
        """
        with self._db.orm_session() as session:
            result = (
                session.query(GovernanceTicketOrm)
                .filter(GovernanceTicketOrm.ticket_id == ticket_id)
                .update(
                    {GovernanceTicketOrm.delivery_status: status},
                    synchronize_session=False,
                )
            )
            return result == 1

    def update_last_notified_at(self, ticket_id: str, ts: datetime | None) -> bool:
        """Update a single ticket's last_notified_at (self-managed session).

        Args:
            ticket_id: 工单稳定 UUID。
            ts: 最近一次成功通知时间(首投/reminder sent 时刷),None 清空。

        Returns:
            True if 1 row updated, False otherwise.
        """
        with self._db.orm_session() as session:
            result = (
                session.query(GovernanceTicketOrm)
                .filter(GovernanceTicketOrm.ticket_id == ticket_id)
                .update(
                    {GovernanceTicketOrm.last_notified_at: ts},
                    synchronize_session=False,
                )
            )
            return result == 1

    def update_remind_count(self, ticket_id: str, remind_count: int) -> bool:
        """Update a single ticket's remind_count (self-managed session)."""
        with self._db.orm_session() as session:
            result = (
                session.query(GovernanceTicketOrm)
                .filter(GovernanceTicketOrm.ticket_id == ticket_id)
                .update(
                    {GovernanceTicketOrm.remind_count: remind_count},
                    synchronize_session=False,
                )
            )
            return result == 1