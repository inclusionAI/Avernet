"""TaskRecord 查询 mixin — 工单读查询(find/list/count)。

从 task_record_repo.py 拆出,使主文件低于 R9 1000 行门禁。
mixin 不定义 ``__init__``;``self._db`` / env 由组合后的
:class:`TaskRecordRepository` 主类提供。查询方法之间无 self 互调。
"""
from __future__ import annotations

from datetime import datetime

from agentclaw.community.core.economy.governance.domain.enums import (
    ACTIVE_STATUSES,
    GovernanceStatus,
)
from agentclaw.community.core.economy.governance.domain.ticket import GovernanceTicket
from agentclaw.community.core.economy.governance.orm import GovernanceTicketOrm
from agentclaw.community.utils.env_utils import get_current_env


class TaskRecordQueryMixin:
    """工单读查询 — find_/list_/count_ 方法,self._db 由主类提供。"""

    def find_active_ticket(
        self, active_worker: str,
    ) -> GovernanceTicket | None:
        """Find the active ticket for an active_worker (owner_id:bot_id).

        Active = governance_status IN ACTIVE_STATUSES
        (open / scheduled / waiting_review)。

        Returns:
            :class:`GovernanceTicket` or ``None`` if no active ticket exists.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            obj = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.active_worker == active_worker,
                    GovernanceTicketOrm.governance_status.in_(ACTIVE_STATUSES),
                    GovernanceTicketOrm.env == _env,
                )
                .one_or_none()
            )
            return GovernanceTicket.from_orm(obj) if obj else None

    def find_by_ticket_id(
        self, ticket_id: str,
    ) -> GovernanceTicket | None:
        """Find a ticket by its stable UUID (ticket_id).

        Returns:
            :class:`GovernanceTicket` or ``None``.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            obj = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.ticket_id == ticket_id,
                    GovernanceTicketOrm.env == _env,
                )
                .one_or_none()
            )
            return GovernanceTicket.from_orm(obj) if obj else None

    def find_latest_closed_by_worker(
        self, worker_id: str,
    ) -> GovernanceTicket | None:
        """Find most recently closed ticket for a worker (cooldown & review_rejected check).

        Ordered by closed_at DESC.

        Returns:
            :class:`GovernanceTicket` or ``None`` if no closed ticket exists.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            obj = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.worker_id == worker_id,
                    GovernanceTicketOrm.governance_status == GovernanceStatus.CLOSED,
                    GovernanceTicketOrm.env == _env,
                )
                .order_by(
                    GovernanceTicketOrm.closed_at.desc(),
                    GovernanceTicketOrm.gmt_modified.desc(),
                )
                .first()
            )
            return GovernanceTicket.from_orm(obj) if obj else None

    def find_observed_ticket(
        self, worker_id: str,
    ) -> GovernanceTicket | None:
        """Find the active OBSERVED ticket for a worker (whitelist observation).

        白名单观察态:bot 进白名单后由审批加白或 scan 兜底转入 OBSERVED,或由
        offline-batch 命中白名单时新建。本查询按 ``worker_id`` 取该 worker 最近
        的一条观察单(同 ``find_latest_closed_by_worker`` 的 worker_id 口径,
        **非** active_worker —— 加白关单时 active_worker 已释放置 NULL)。

        排序 ``gmt_modified DESC``(非 closed_at,因 OBSERVED 不设 closed_at);
        最新刷新的观察单排前,供 offline-batch 刷新与删白收尾定位。

        Returns:
            :class:`GovernanceTicket` or ``None`` if no observed ticket exists.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            obj = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.worker_id == worker_id,
                    GovernanceTicketOrm.governance_status == GovernanceStatus.OBSERVED,
                    GovernanceTicketOrm.env == _env,
                )
                .order_by(
                    GovernanceTicketOrm.gmt_modified.desc(),
                )
                .first()
            )
            return GovernanceTicket.from_orm(obj) if obj else None

    def find_latest_tickets_by_worker_keys(
        self, worker_keys: list[str],
    ) -> dict[str, GovernanceTicket]:
        """Batch: most-recent ticket per worker_key (any status/close_reason).

        一条 IN 查询取所有候选(按 ``gmt_create`` DESC)+ Python 侧 group by
        ``worker_id`` 各取首条,避免每 worker point query(N+1)。

        用 ``worker_id``((始终 ``owner_id:bot_id``)而非 ``active_worker``
        (closed 后置 NULL)—— 含历史 closed 工单,正是白单叠加所需。

        Args:
            worker_keys: ``owner_id:bot_id`` 形式的 worker key 集合。

        Returns:
            ``{worker_key: 最近一条 GovernanceTicket}``;无工单的 worker
            不出现在 dict。空输入短路返回 ``{}``。
        """
        if not worker_keys:
            return {}
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            rows = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.worker_id.in_(worker_keys),
                    GovernanceTicketOrm.env == _env,
                )
                .order_by(GovernanceTicketOrm.gmt_create.desc())
                .all()
            )
        latest: dict[str, GovernanceTicket] = {}
        for row in rows:
            # gmt_create DESC 已排序,首次见到的 worker_id 即该 worker 最近一条。
            if row.worker_id in latest:
                continue
            latest[row.worker_id] = GovernanceTicket.from_orm(row)
        return latest

    def list_recent_tickets_by_worker(
        self,
        *,
        worker_id: str | None = None,
        owner_id: str | None = None,
        bot_id: str | None = None,
        limit: int = 5,
    ) -> list[GovernanceTicket]:
        """按 worker 取最近 N 条工单(全状态,gmt_create 倒序)。

        worker_id(owner:bot)精确等值;owner_id/bot_id 独立维度可任传其一或组合
        (组合 = AND,同一工单同时满足)。全状态(open/scheduled/waiting_review/
        closed/observed)不过滤,含历史关单 + 当前活跃单,供管理员横向看一个
        worker 的工单生命周期,辅助关单-重开决策。

        语义"取最近 N 条",非分页;无 total 配套(调用方 service 已据此设计响应)。
        用 ``worker_id`` 而非 ``active_worker``(closed 后置 NULL),口径与
        :meth:`find_latest_closed_by_worker` 一致。

        Args:
            worker_id: 复合标识,优先(调用方已解析格式并校验,本层不再校验)。
            owner_id: 按 owner 维度。
            bot_id: 按 bot 维度。
            limit: 取数上限(1~50,调用方已校验)。

        Returns:
            最近 N 条 :class:`GovernanceTicket`(gmt_create DESC);无匹配返回
            ``[]``。三定位参皆空时返回 ``[]``(防全表扫兜底,调用方 service 层
            通常已拦 400,此处双保险)。
        """
        if worker_id is None and owner_id is None and bot_id is None:
            return []
        _env = get_current_env()
        with self._db.orm_session() as s:
            q = (
                s.query(GovernanceTicketOrm)
                .filter(GovernanceTicketOrm.env == _env)
            )
            if worker_id is not None:
                q = q.filter(GovernanceTicketOrm.worker_id == worker_id)
            if owner_id is not None:
                q = q.filter(GovernanceTicketOrm.owner_id == owner_id)
            if bot_id is not None:
                q = q.filter(GovernanceTicketOrm.bot_id == bot_id)
            rows = (
                q.order_by(GovernanceTicketOrm.gmt_create.desc())
                .limit(limit)
                .all()
            )
            return [GovernanceTicket.from_orm(r) for r in rows]

    def list_active_open_tickets(
        self,
    ) -> list[GovernanceTicket]:
        """List all open tickets with active_worker set (for auto_silence).

        Used by offline-batch to find active open tickets not in current batch.

        Returns:
            List of :class:`GovernanceTicket`.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.governance_status == GovernanceStatus.OPEN,
                    GovernanceTicketOrm.active_worker.isnot(None),
                    GovernanceTicketOrm.env == _env,
                )
                .all()
            )
            return [GovernanceTicket.from_orm(r) for r in rows]

    def list_scheduled_due(
        self, now: datetime,
    ) -> list[GovernanceTicket]:
        """Find scheduled tickets where mute_until <= now (schedule_due).

        These tickets should transition from scheduled -> waiting_review.

        Returns:
            List of :class:`GovernanceTicket`.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.governance_status == GovernanceStatus.SCHEDULED,
                    GovernanceTicketOrm.mute_until <= now,
                    GovernanceTicketOrm.mute_until.isnot(None),
                    GovernanceTicketOrm.env == _env,
                )
                .all()
            )
            return [GovernanceTicket.from_orm(r) for r in rows]

    def list_auto_silence_eligible(
        self,
        *,
        min_consecutive_days: int,
    ) -> list[GovernanceTicket]:
        """Find open tickets eligible for auto-silence convergence (7.2.6).

        Conditions: governance_status='open' + latest_decision='normal' +
        consecutive_normal_days >= min_consecutive_days + active_worker set.

        Args:
            min_consecutive_days: ``auto_silence_close_days`` from config.

        Returns:
            List of :class:`GovernanceTicket` meeting the convergence threshold.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.governance_status == GovernanceStatus.OPEN,
                    GovernanceTicketOrm.latest_decision == "normal",
                    GovernanceTicketOrm.consecutive_normal_days
                    >= min_consecutive_days,
                    GovernanceTicketOrm.active_worker.isnot(None),
                    GovernanceTicketOrm.env == _env,
                )
                .all()
            )
            return [GovernanceTicket.from_orm(r) for r in rows]

    def list_remindable_tickets(
        self, now: datetime,
    ) -> list[GovernanceTicket]:
        """Find tickets eligible for reminder creation (7.3.2).

        Conditions: open + latest_decision=actionable + remind_at <= now
        + remind_at IS NOT NULL + response IS NULL + active_worker set.

        Returns:
            List of :class:`GovernanceTicket`.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.governance_status == GovernanceStatus.OPEN,
                    GovernanceTicketOrm.latest_decision == "actionable",
                    GovernanceTicketOrm.remind_at <= now,
                    GovernanceTicketOrm.remind_at.isnot(None),
                    GovernanceTicketOrm.response.is_(None),
                    GovernanceTicketOrm.active_worker.isnot(None),
                    GovernanceTicketOrm.env == _env,
                )
                .all()
            )
            return [GovernanceTicket.from_orm(r) for r in rows]

    def list_tickets_by_owner_and_statuses(
        self,
        owner_id: str,
        statuses: list[str],
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> list[GovernanceTicket]:
        """Owner's tickets in the given statuses, newest first, paged.

        Returns:
            List of :class:`GovernanceTicket`.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.owner_id == owner_id,
                    GovernanceTicketOrm.governance_status.in_(statuses),
                    GovernanceTicketOrm.env == _env,
                )
                .order_by(GovernanceTicketOrm.gmt_create.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [GovernanceTicket.from_orm(r) for r in rows]

    def list_tickets_by_statuses(
        self,
        statuses: list[str],
        *,
        offset: int = 0,
        limit: int = 50,
        delivery_statuses: list[str] | None = None,
    ) -> list[GovernanceTicket]:
        """All tickets in the given statuses (cross-owner), newest first, paged.

        评审场景:按治理状态过滤工单(活跃 / 待审阅 / 已关闭),跨 owner。

        Args:
            statuses: 治理状态白名单(open/scheduled/waiting_review/closed)。
            offset: 分页偏移。
            limit: 分页上限。
            delivery_statuses: 投递状态白名单(pending/sent/failed/cancelled),
                None 不过滤,空列表短路返回空列表(对齐 statuses 行为)。

        Returns:
            List of :class:`GovernanceTicket` (gmt_create 由 from_orm 灌入)。
        """
        if not statuses:
            return []
        if delivery_statuses is not None and not delivery_statuses:
            return []
        _env = get_current_env()
        with self._db.orm_session() as s:
            q = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.governance_status.in_(statuses),
                    GovernanceTicketOrm.env == _env,
                )
            )
            if delivery_statuses:
                q = q.filter(GovernanceTicketOrm.delivery_status.in_(delivery_statuses))
            rows = (
                q.order_by(GovernanceTicketOrm.gmt_create.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [GovernanceTicket.from_orm(r) for r in rows]

    def count_tickets_by_statuses(
        self,
        statuses: list[str],
        delivery_statuses: list[str] | None = None,
    ) -> int:
        """Count all tickets in the given statuses (cross-owner, paged-list 配套)。

        Args:
            statuses: 治理状态白名单。
            delivery_statuses: 投递状态白名单,None 不过滤,空列表短路返回 0。

        Returns:
            满足条件的工单总数(与 list_tickets_by_statuses 同阶过滤)。
        """
        if not statuses:
            return 0
        if delivery_statuses is not None and not delivery_statuses:
            return 0
        _env = get_current_env()
        with self._db.orm_session() as s:
            q = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.governance_status.in_(statuses),
                    GovernanceTicketOrm.env == _env,
                )
            )
            if delivery_statuses:
                q = q.filter(GovernanceTicketOrm.delivery_status.in_(delivery_statuses))
            return q.count()

    def count_active_open(
        self,
    ) -> int:
        """Count all active open tickets (for admin dashboard)."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            return (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.governance_status.in_(ACTIVE_STATUSES),
                    GovernanceTicketOrm.active_worker.isnot(None),
                    GovernanceTicketOrm.env == _env,
                )
                .count()
            )

    def find_ticket_by_notification_id(
        self, notification_id: str,
    ) -> GovernanceTicket | None:
        """Find a ticket via its notify_log's notification_id.

        Used by feedback_service: notification_id -> notify_log.ticket_id -> task_record.

        Returns:
            :class:`GovernanceTicket` or ``None``.
        """
        from agentclaw.community.core.economy.governance.orm import GovernanceNotificationOrm

        _env = get_current_env()
        with self._db.orm_session() as s:
            notify_row = (
                s.query(GovernanceNotificationOrm)
                .filter(
                    GovernanceNotificationOrm.notification_id == notification_id,
                    GovernanceNotificationOrm.env == _env,
                )
                .first()
            )
            if notify_row is None or notify_row.ticket_id is None:
                return None
            obj = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.ticket_id == notify_row.ticket_id,
                    GovernanceTicketOrm.env == _env,
                )
                .one_or_none()
            )
            return GovernanceTicket.from_orm(obj) if obj else None