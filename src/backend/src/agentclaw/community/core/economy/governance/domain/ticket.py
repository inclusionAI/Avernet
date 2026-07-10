"""领域模型 — GovernanceTicket 工单生命周期。

与 GovernanceNotification / WhitelistEntry 同级,按实体拆文件。
共享基础(MutableSnapshot / TICKET_TRANSITIONS /
IllegalTicketTransitionError / _iso)在 ``domain.py``;ORM 映射见
``repositories/orm.py``;repo 用 from_orm/to_orm/apply_to 做翻译边界。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from agentclaw.community.core.economy.governance.domain.enums import (
    GovernanceStatus,
)
from agentclaw.community.core.economy.governance.domain.domain import (
    IllegalTicketTransitionError,
    MutableSnapshot,
    TICKET_TRANSITIONS,
    _iso,
)


@dataclass(slots=True)
class GovernanceTicket:
    """工单领域模型 — service 层唯一接触的对象。

    属性命名对齐 ORM business property(非 Column 原名),
    让 service 从 ORM 迁移到 domain 时属性访问零改动。

    不变量:
      - 身份 逻辑不可变(由工厂 create 一次性赋值)
      - 快照 只能通过 refresh_snapshot 方法替换(离线批处理驱动)
      - 生命周期 只能通过状态机方法变更(transition_to / accept_feedback
        / close / pause / resume)
      - sealed 列(id/env)不在本模型上;gmt_create/gmt_modified 作为只读
        基础元信息保留(由 from_orm 灌入,展示/排序用)
    """

    # ── 身份(创建时写入,逻辑不可变) ──────────────
    ticket_id: str | None
    worker_id: str
    bot_id: str | None
    owner_id: str | None
    bot_name: str | None
    _snapshot: MutableSnapshot

    # ── 生命周期态(可变,受状态机守卫) ──────────────
    governance_status: GovernanceStatus
    assignee: str | None             # ORM: active_worker
    user_feedback: str | None        # ORM: response
    feedback_at: datetime | None     # ORM: response_at
    feedback_remark: str | None      # ORM: response_remark
    feedback_source: str | None      # ORM: response_source
    close_reason: str | None
    closed_at: datetime | None
    cooldown_until: datetime | None
    review_reason: str | None
    review_decision: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_remark: str | None
    repair_deadline: datetime | None
    resume_at: datetime | None       # ORM: mute_until
    remind_at: datetime | None
    remind_count: int
    feedback_payload: str | None
    actor_id: str | None
    # ── 基础元信息(只读,由 from_orm 从 sealed 列灌入) ───
    gmt_create: datetime | None = None
    gmt_modified: datetime | None = None

    # ── 快照访问(只读) ──────────────────────────────

    @property
    def snapshot(self) -> MutableSnapshot:
        """可变快照 — 外部应只读,写入走 refresh_snapshot。"""
        return self._snapshot

    # ── 快照只读委托 ──────────────────────────────

    @property
    def dt_version(self) -> str:
        """数据版本标识 — 快照委托。"""
        return self._snapshot.dt_version

    @property
    def initial_decision(self) -> str:
        """创建时决策 — 快照委托(永远='actionable', §5.6)。"""
        return self._snapshot.initial_decision

    @property
    def current_decision(self) -> str | None:
        """最新决策 — 快照委托。"""
        return self._snapshot.current_decision

    @property
    def triggered_dimensions(self) -> str | None:
        """触发的治理维度 — 快照委托。"""
        return self._snapshot.triggered_dimensions

    @property
    def hit_dimensions_count(self) -> int | None:
        """命中维度数 — 快照委托。"""
        return self._snapshot.hit_dimensions_count

    @property
    def severity(self) -> str | None:
        """严重等级 — 快照委托。"""
        return self._snapshot.severity

    @property
    def estimated_saving_tokens(self) -> int | None:
        """预估节省 token — 快照委托。"""
        return self._snapshot.estimated_saving_tokens

    @property
    def saving_ratio(self) -> float | None:
        """节省比例 — 快照委托。"""
        return self._snapshot.saving_ratio

    @property
    def task_summary(self) -> str | None:
        """任务摘要 — 快照委托。"""
        return self._snapshot.task_summary

    @property
    def notification_structured(self) -> str | None:
        """原始 JSON 结构 — 快照委托。"""
        return self._snapshot.notification_structured

    @property
    def analysis_status(self) -> str | None:
        """分析状态 — 快照委托。"""
        return self._snapshot.analysis_status

    @property
    def consecutive_normal_days(self) -> int:
        """连续 normal 天数 — 快照委托。"""
        return self._snapshot.consecutive_normal_days

    @property
    def last_decision_dt_version(self) -> str | None:
        """最近决策的数据版本 — 快照委托。"""
        return self._snapshot.last_decision_dt_version

    @property
    def last_seen_at(self) -> datetime | None:
        """最近一次命中 actionable 的时间 — 快照委托。"""
        return self._snapshot.last_seen_at

    @property
    def last_sync_at(self) -> datetime | None:
        """最近一次离线同步时间 — 快照委托。"""
        return self._snapshot.last_sync_at

    # ── 业务 property ──────────────────────────────

    @property
    def is_open(self) -> bool:
        """工单是否处于 open 状态。"""
        return self.governance_status == GovernanceStatus.OPEN

    @property
    def is_active(self) -> bool:
        """工单是否活跃(尚未关闭)。"""
        return self.governance_status in (
            GovernanceStatus.OPEN, GovernanceStatus.SCHEDULED,
            GovernanceStatus.WAITING_REVIEW,
        )

    @property
    def is_actionable(self) -> bool:
        """当前决策是否仍需处理 — 决定是否发送/提醒。"""
        return self.current_decision == "actionable"

    @property
    def has_feedback(self) -> bool:
        """用户是否已反馈。"""
        return self.user_feedback is not None

    def can_accept_feedback(self) -> bool:
        """§7.4.1: 仅 open + 未反馈 才接受。"""
        return self.governance_status == GovernanceStatus.OPEN and self.user_feedback is None

    # ── 状态机行为 ──────────────────────────────────────

    def transition_to(self, target: GovernanceStatus) -> None:
        """状态机白名单转换。

        Args:
            target: 目标状态。

        Raises:
            IllegalTicketTransitionError: 转换不在 TICKET_TRANSITIONS 白名单中。
        """
        allowed = TICKET_TRANSITIONS.get(self.governance_status, frozenset())
        if target not in allowed:
            raise IllegalTicketTransitionError(
                f"{self.governance_status.value} -> {target.value} not allowed"
            )
        self.governance_status = target

    def accept_feedback(
        self,
        *,
        user_feedback: str,
        feedback_at: datetime,
        feedback_source: str,
        target_status: GovernanceStatus,
        feedback_remark: str | None = None,
        repair_deadline: datetime | None = None,
        resume_at: datetime | None = None,
        review_reason: str | None = None,
        actor_id: str | None = None,
        feedback_payload: str | None = None,
    ) -> None:
        """接受用户反馈 — optimized / need_time / dispute / whitelist。

        Args:
            user_feedback: 反馈类型(optimized/need_time/dispute/whitelist)。
            feedback_at: 反馈时间。
            feedback_source: 反馈来源(http_api/card_callback/admin_api)。
            target_status: 目标状态(scheduled for need_time,
                waiting_review for others)。
            feedback_remark: 反馈备注。
            repair_deadline: 修复截止日期(need_time 必填)。
            resume_at: 恢复时间(need_time: repair_deadline + cooldown_days)。
            review_reason: 审核原因(user_optimized/user_disputed等)。
            actor_id: 实际操作人 ID。
            feedback_payload: 结构化反馈 JSON。
        """
        self.transition_to(target_status)
        self.user_feedback = user_feedback
        self.feedback_at = feedback_at
        self.feedback_source = feedback_source
        self.feedback_remark = feedback_remark
        self.repair_deadline = repair_deadline
        self.resume_at = resume_at
        self.review_reason = review_reason
        self.actor_id = actor_id
        self.feedback_payload = feedback_payload

    def close(
        self,
        *,
        close_reason: str,
        closed_at: datetime,
        cooldown_until: datetime | None = None,
    ) -> None:
        """关闭工单。

        Args:
            close_reason: 关闭原因。
            closed_at: 关闭时间。
            cooldown_until: 冷却截止时间。
        """
        self.transition_to(GovernanceStatus.CLOSED)
        self.close_reason = close_reason
        self.closed_at = closed_at
        self.cooldown_until = cooldown_until
        self.assignee = None  # closed 释放 active_worker

    def pause(self, *, review_reason: str) -> None:
        """暂停工单 — 进入 waiting_review。

        Args:
            review_reason: 暂停原因(admin_paused/schedule_due/...)。
        """
        self.transition_to(GovernanceStatus.WAITING_REVIEW)
        self.review_reason = review_reason

    def resume(self) -> None:
        """恢复暂停工单 — waiting_review → open。"""
        self.transition_to(GovernanceStatus.OPEN)

    def refresh_snapshot(self, **fields: object) -> None:
        """替换快照 — 离线批处理刷新数据后调用。

        创建新 MutableSnapshot 替换 _snapshot,保证单入口。

        Args:
            **fields: 传入需要更新的快照字段(新值)。
        """
        self._snapshot = replace(self._snapshot, **fields)

    # ── 工厂 ─────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        *,
        ticket_id: str | None,
        worker_id: str,
        bot_id: str | None,
        owner_id: str | None,
        bot_name: str | None,
        snapshot: MutableSnapshot,
        assignee: str | None = None,
    ) -> GovernanceTicket:
        """领域构造:创建即赋快照,状态初值 OPEN。

        Args:
            ticket_id: 工单稳定 UUID。
            worker_id: owner_id:bot_id。
            bot_id: Bot ID。
            owner_id: 负责人 ID。
            bot_name: Bot 名称。
            snapshot: 可变快照(创建时一次性写入)。
            assignee: 工单持有人(active=worker_id; closed=None)。

        Returns:
            初始化的领域模型实例,governance_status=OPEN。
        """
        return cls(
            ticket_id=ticket_id,
            worker_id=worker_id,
            bot_id=bot_id,
            owner_id=owner_id,
            bot_name=bot_name,
            _snapshot=snapshot,
            governance_status=GovernanceStatus.OPEN,
            assignee=assignee or worker_id,
            user_feedback=None,
            feedback_at=None,
            feedback_remark=None,
            feedback_source=None,
            close_reason=None,
            closed_at=None,
            cooldown_until=None,
            review_reason=None,
            review_decision=None,
            reviewed_by=None,
            reviewed_at=None,
            review_remark=None,
            repair_deadline=None,
            resume_at=None,
            remind_at=None,
            remind_count=0,
            feedback_payload=None,
            actor_id=None,
            gmt_create=None,
            gmt_modified=None,
        )

    # ── 翻译边界 ─────────────────────────────────────────

    @classmethod
    def from_orm(cls, obj: object) -> GovernanceTicket:
        """读翻译:ORM → 领域模型。

        Args:
            obj: orm.GovernanceTicketOrm 实例(ORM 对象)。

        Returns:
            领域模型实例。sealed 列(id/env)不会被映射；gmt_create/gmt_modified 作为
            基础只读元信息灌入(评审/展示场景需读取创建时间)。
        """
        _saving_ratio = obj.saving_ratio
        if _saving_ratio is not None:
            _saving_ratio = float(_saving_ratio)
        return cls(
            # 身份
            ticket_id=obj.ticket_id,
            worker_id=obj.worker_id,
            bot_id=obj.bot_id,
            owner_id=obj.owner_id,
            bot_name=obj.bot_name,
            # 可变快照
            _snapshot=MutableSnapshot(
                dt_version=obj.dt_version or "",
                initial_decision=obj.governance_decision or "actionable",
                current_decision=obj.latest_decision,
                triggered_dimensions=obj.hit_dimensions,
                hit_dimensions_count=obj.hit_dimensions_count,
                severity=obj.governance_max_priority,
                estimated_saving_tokens=obj.expected_token_saving,
                saving_ratio=_saving_ratio,
                task_summary=obj.task_summary,
                notification_structured=obj.notification_structured,
                analysis_status=obj.analysis_status,
                consecutive_normal_days=obj.consecutive_normal_days or 0,
                last_decision_dt_version=obj.last_decision_dt_version,
                last_seen_at=obj.last_seen_at,
                last_sync_at=obj.last_sync_at,
            ),
            # 生命周期态
            governance_status=GovernanceStatus(obj.governance_status or "open"),
            assignee=obj.active_worker,
            user_feedback=obj.response,
            feedback_at=obj.response_at,
            feedback_remark=obj.response_remark,
            feedback_source=obj.response_source,
            close_reason=obj.close_reason,
            closed_at=obj.closed_at,
            cooldown_until=obj.cooldown_until,
            review_reason=obj.review_reason,
            review_decision=obj.review_decision,
            reviewed_by=obj.reviewed_by,
            reviewed_at=obj.reviewed_at,
            review_remark=obj.review_remark,
            repair_deadline=obj.repair_deadline,
            resume_at=obj.mute_until,
            remind_at=obj.remind_at,
            remind_count=obj.remind_count or 0,
            feedback_payload=obj.feedback_payload,
            actor_id=obj.actor_id,
            gmt_create=getattr(obj, "gmt_create", None),
            gmt_modified=getattr(obj, "gmt_modified", None),
        )

    def to_orm(self, row: object | None = None) -> object:
        """写翻译:领域模型 → ORM。

        新建时传 row=None 会创建 ORM 对象;更新已有行传 row。
        sealed 列(id/env)不在领域模型上;gmt_create/gmt_modified 不写回
        (由数据库 default/onupdate 维护,领域模型仅读不写)。

        Args:
            row: 可选已有 ORM 行;None 时新建。

        Returns:
            ORM 对象(已赋值,可 s.add)。
        """
        from agentclaw.community.core.economy.governance.repositories.orm import (
            GovernanceTicketOrm,
        )
        row = row or GovernanceTicketOrm()
        # 身份
        row.ticket_id = self.ticket_id
        row.worker_id = self.worker_id
        row.bot_id = self.bot_id
        row.owner_id = self.owner_id
        row.bot_name = self.bot_name
        # 可变快照
        s = self._snapshot
        row.dt_version = s.dt_version
        row.governance_decision = s.initial_decision
        row.latest_decision = s.current_decision
        row.hit_dimensions = s.triggered_dimensions
        row.hit_dimensions_count = s.hit_dimensions_count
        row.governance_max_priority = s.severity
        row.expected_token_saving = s.estimated_saving_tokens
        row.saving_ratio = s.saving_ratio
        row.task_summary = s.task_summary
        row.notification_structured = s.notification_structured
        row.analysis_status = s.analysis_status
        row.consecutive_normal_days = s.consecutive_normal_days
        row.last_decision_dt_version = s.last_decision_dt_version
        row.last_seen_at = s.last_seen_at
        row.last_sync_at = s.last_sync_at
        # 生命周期态
        row.active_worker = self.assignee
        row.governance_status = self.governance_status.value
        row.response = self.user_feedback
        row.response_at = self.feedback_at
        row.response_remark = self.feedback_remark
        row.response_source = self.feedback_source
        row.close_reason = self.close_reason
        row.closed_at = self.closed_at
        row.cooldown_until = self.cooldown_until
        row.review_reason = self.review_reason
        row.review_decision = self.review_decision
        row.reviewed_by = self.reviewed_by
        row.reviewed_at = self.reviewed_at
        row.review_remark = self.review_remark
        row.repair_deadline = self.repair_deadline
        row.mute_until = self.resume_at
        row.remind_at = self.remind_at
        row.remind_count = self.remind_count
        row.feedback_payload = self.feedback_payload
        row.actor_id = self.actor_id
        return row

    def apply_to(self, row: object) -> None:
        """增量写翻译:只把可变生命周期态写回已有 ORM,不碰快照/sealed。

        用于 update 场景:读取 ORM 行 → 修改领域模型 → apply_to 写回。

        Args:
            row: 已有 ORM 行(从 session 查出)。
        """
        row.active_worker = self.assignee
        row.governance_status = self.governance_status.value
        row.response = self.user_feedback
        row.response_at = self.feedback_at
        row.response_remark = self.feedback_remark
        row.response_source = self.feedback_source
        row.close_reason = self.close_reason
        row.closed_at = self.closed_at
        row.cooldown_until = self.cooldown_until
        row.review_reason = self.review_reason
        row.review_decision = self.review_decision
        row.reviewed_by = self.reviewed_by
        row.reviewed_at = self.reviewed_at
        row.review_remark = self.review_remark
        row.repair_deadline = self.repair_deadline
        row.mute_until = self.resume_at
        row.remind_at = self.remind_at
        row.remind_count = self.remind_count
        row.feedback_payload = self.feedback_payload
        row.actor_id = self.actor_id

    def to_dict(self) -> dict:
        """API 序列化 — router 直接 ``data=[t.to_dict() for t in items]``。

        字段名对齐 ORM 列名(API 契约,前端依赖);sealed 列
        (id/env)不在领域模型上,不暴露。gmt_create/gmt_modified 暴露(只读元信息)。
        时间字段转 ISO 字符串以便 JSON 序列化。
        """
        s = self._snapshot
        return {
            "ticket_id": self.ticket_id,
            "worker_id": self.worker_id,
            "bot_id": self.bot_id,
            "bot_name": self.bot_name,
            "owner_id": self.owner_id,
            "dt_version": s.dt_version,
            "governance_decision": s.initial_decision,
            "latest_decision": s.current_decision,
            "hit_dimensions": s.triggered_dimensions,
            "hit_dimensions_count": s.hit_dimensions_count,
            "governance_max_priority": s.severity,
            "expected_token_saving": s.estimated_saving_tokens,
            "saving_ratio": s.saving_ratio,
            "task_summary": s.task_summary,
            "notification_structured": s.notification_structured,
            "analysis_status": s.analysis_status,
            "consecutive_normal_days": s.consecutive_normal_days,
            "last_decision_dt_version": s.last_decision_dt_version,
            "last_seen_at": _iso(s.last_seen_at),
            "last_sync_at": _iso(s.last_sync_at),
            "active_worker": self.assignee,
            "governance_status": self.governance_status.value,
            "response": self.user_feedback,
            "response_at": _iso(self.feedback_at),
            "response_remark": self.feedback_remark,
            "response_source": self.feedback_source,
            "close_reason": self.close_reason,
            "closed_at": _iso(self.closed_at),
            "cooldown_until": _iso(self.cooldown_until),
            "review_reason": self.review_reason,
            "review_decision": self.review_decision,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": _iso(self.reviewed_at),
            "review_remark": self.review_remark,
            "repair_deadline": _iso(self.repair_deadline),
            "mute_until": _iso(self.resume_at),
            "remind_at": _iso(self.remind_at),
            "remind_count": self.remind_count,
            "feedback_payload": self.feedback_payload,
            "actor_id": self.actor_id,
            "gmt_create": _iso(self.gmt_create),
            "gmt_modified": _iso(self.gmt_modified),
        }