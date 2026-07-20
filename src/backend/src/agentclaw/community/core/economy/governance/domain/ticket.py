"""领域模型 — GovernanceTicket 工单生命周期。

与 GovernanceNotification / WhitelistEntry 同级,按实体拆文件。
MutableSnapshot / TICKET_TRANSITIONS / IllegalTicketTransitionError 本文件内联;
_iso 共享工具在 ``base.py``;ORM 映射见 ``repositories/orm.py``;
repo 用 from_orm/to_orm/apply_to 做翻译边界。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from agentclaw.community.core.economy.governance.domain.base import (
    _iso,
    _normalize_delivery_status,
)
from agentclaw.community.core.economy.governance.domain.enums import (
    CloseReason,
    GovernanceStatus,
    TicketAction,
)


class IllegalTicketTransitionError(ValueError):
    """工单状态非法转换。"""


# ── 可变快照(离线批处理可刷新) ────────────────────


@dataclass(slots=True)
class MutableSnapshot:
    """可刷新快照 — 离线批处理通过 GovernanceTicket.refresh_snapshot 替换。

    与 FrozenSnapshot 不同:MutableSnapshot 可替换(非 frozen)。
    外部不直接赋值字段,而是通过 ``GovernanceTicket.refresh_snapshot``
    创建新 MutableSnapshot 替换 _snapshot,保证单入口。

    对应 ORM 列:
      - dt_version               ← orm.dt_version
      - initial_decision         ← orm.governance_decision (永远='actionable')
      - current_decision         ← orm.latest_decision
      - triggered_dimensions     ← orm.hit_dimensions
      - hit_dimensions_count     ← orm.hit_dimensions_count
      - severity                 ← orm.governance_max_priority
      - estimated_saving_tokens  ← orm.expected_token_saving
      - saving_ratio             ← orm.saving_ratio
      - token_baseline          ← orm.token_baseline (guard: 非 None 才刷新, 见 lifecycle_service)
      - task_summary             ← orm.task_summary
      - notification_structured  ← orm.notification_structured
      - analysis_status          ← orm.analysis_status
      - consecutive_normal_days  ← orm.consecutive_normal_days
      - last_decision_dt_version ← orm.last_decision_dt_version
      - last_seen_at             ← orm.last_seen_at
      - last_sync_at             ← orm.last_sync_at
    """

    dt_version: str
    initial_decision: str
    current_decision: str | None
    triggered_dimensions: str | None
    hit_dimensions_count: int | None
    severity: str | None
    estimated_saving_tokens: int | None
    saving_ratio: float | None
    task_summary: str | None
    notification_structured: str | None
    analysis_status: str | None
    consecutive_normal_days: int
    last_decision_dt_version: str | None
    last_seen_at: datetime | None
    last_sync_at: datetime | None
    delivery_status: str = "pending"  # 最近通知投递状态单值: pending/sent/failed/cancelled
    last_notified_at: datetime | None = None  # 最近一次成功通知时间(首投/reminder sent时刷)
    token_baseline: int | None = None


# ── 状态机转换表(工单) ─────────────────────────
# 合法转换: {当前状态: {允许的目标状态集合}}
TICKET_TRANSITIONS: dict[GovernanceStatus, frozenset[GovernanceStatus]] = {
    GovernanceStatus.OPEN: frozenset({
        GovernanceStatus.SCHEDULED, GovernanceStatus.WAITING_REVIEW,
        GovernanceStatus.OBSERVED, GovernanceStatus.CLOSED,
    }),
    GovernanceStatus.SCHEDULED: frozenset({
        GovernanceStatus.WAITING_REVIEW, GovernanceStatus.OBSERVED,
        GovernanceStatus.CLOSED,
    }),
    GovernanceStatus.WAITING_REVIEW: frozenset({
        GovernanceStatus.OPEN, GovernanceStatus.SCHEDULED,
        GovernanceStatus.OBSERVED, GovernanceStatus.CLOSED,
    }),
    # OBSERVED = 白名单观察态。仅可被删白收尾转 CLOSED;不回活跃态
    # (删白后由 offline-batch 正常 Step6 重建新 OPEN 单,而非复活同单)。
    GovernanceStatus.OBSERVED: frozenset({GovernanceStatus.CLOSED}),
    GovernanceStatus.CLOSED: frozenset(),
}


# ── feedback_verdict 纯函数(user⊗admin 成对派生,零跨层依赖) ──────
# pair 表直接用 response 原值(optimized/dispute/whitelist/need_time),不做中间
# 归一化翻译——系统别处不用的中间词只会增加理解负担。离线侧可直接 import 复用。

# (response, review_decision) → verdict(双流齐备的成对结果)
# review 四态:approve_close/approve_scheduled[新]/approve_whitelist/reject_for_reopen。
# 命名贴合四反馈语义;need_time 同意走 approve_scheduled(→schedule_confirmed),
# approve_close 对 need_time 不作正常路径(available_actions 不下发)落 other。
_VERDICT_PAIR: dict[tuple[str, str], str] = {
    ("optimized", "approve_close"): "confirmed",
    ("optimized", "reject_for_reopen"): "optimized_rejected",
    ("optimized", "approve_whitelist"): "admin_overroled_whitelist",
    ("dispute", "approve_close"): "dispute_accepted",
    ("dispute", "reject_for_reopen"): "dispute_rejected",
    ("whitelist", "approve_whitelist"): "whitelist_confirmed",
    ("whitelist", "reject_for_reopen"): "whitelist_denied",
    ("whitelist", "approve_close"): "whitelist_dismissed",
    ("need_time", "approve_scheduled"): "schedule_confirmed",
    ("need_time", "reject_for_reopen"): "schedule_rejected",
}

# response → pending_review_* (review 缺席时,按用户决策细分)
_VERDICT_PENDING: dict[str, str] = {
    "optimized": "pending_review_optimized",
    "whitelist": "pending_review_whitelist",
    "dispute": "pending_review_dispute",
    "need_time": "pending_review_need_time",
}


def compute_feedback_verdict(
    response: str | None,
    review_decision: str | None,
    governance_status: GovernanceStatus | str | None,
) -> str:
    """用户反馈 ⊗ 管理员 review 的成对裁决结果(读时派生,纯函数,不落库)。

    输入三个既有 task_record 字段,产出成对 verdict 字符串。review 缺席
    (review_decision 空)走 ``pending_review_*``;用户未反馈走
    ``awaiting_user_feedback``/``admin_only_*``;缺省落 ``other``。

    纯函数:无 self/session/DB 依赖,在线自循环与离线侧 import 同源复用。

    Args:
        response: 用户原始反馈(optimized/need_time/dispute/whitelist),None=未反馈。
        review_decision: 管理员审批(approve_close/approve_scheduled/approve_whitelist/
                reject_for_reopen),None=未审。
        governance_status: 工单状态(open/scheduled/waiting_review/closed)。

    Returns:
        verdict 字符串(见模块 ``_VERDICT_PAIR`` / ``_VERDICT_PENDING`` 系列)。
    """
    if review_decision:
        if not response:
            return f"admin_only_{review_decision}"
        return _VERDICT_PAIR.get((response, review_decision), "other")
    # review 缺席:待审或未到 review 阶段
    if not response:
        return "awaiting_user_feedback"
    return _VERDICT_PENDING.get(response, "other")


# ── available_actions 纯函数(按用户反馈下发可做 review 动作) ──────
# review 覆盖四种反馈(need_time 改为进 waiting_review 待审)。按反馈给不同"同意"
# 动作 + 通用 reject。加白(approve_whitelist)是 whitelist 反馈的同意裁决,与运维
# 独立一键加白(/admin/whitelist)出发点不同、并存。label 按反馈差异化,后端下发。

_REVIEW_ENDPOINT = "POST /api/economy/governance/workflow/tickets/review"

# 同意动作的 label 按反馈差异化(避免笼统"批准关闭"丢语义)
_APPROVE_LABEL: dict[str, str] = {
    "optimized": "确认已优化",
    "dispute": "采纳申诉",
    "whitelist": "同意加白",
    "need_time": "同意排期",
}
# 驳回 label 按反馈差异化
_REJECT_LABEL: dict[str, str] = {
    "optimized": "不认可,重开",
    "dispute": "驳回申诉,重开",
    "whitelist": "驳回加白,重开",
    "need_time": "不认可,重开",
}
# 各反馈的"同意"动作
_APPROVE_ACTION: dict[str, TicketAction] = {
    "optimized": TicketAction.APPROVE_CLOSE,
    "dispute": TicketAction.APPROVE_CLOSE,
    "whitelist": TicketAction.APPROVE_WHITELIST,
    "need_time": TicketAction.APPROVE_SCHEDULED,
}


def _action_info(
    action: TicketAction, label: str, remark_required: bool,
) -> dict[str, Any]:
    """构造单个动作描述(前端动态渲染用)。"""
    return {
        "value": action.value,
        "label": label,
        "endpoint": _REVIEW_ENDPOINT,
        "remark_required": remark_required,
    }


def compute_available_actions(user_feedback: str | None) -> list[dict[str, Any]]:
    """按用户反馈类型返回可做的 review 动作列表(纯函数,在线/离线复用同源)。

    Args:
        user_feedback: 用户原始反馈(optimized/need_time/dispute/whitelist);
            None 或未知值 → [](非 review 流程不发动作)。

    Returns:
        动作 dict 列表,每项含 value/label/endpoint/remark_required。 approve 类
        动作在前(同意),reject_for_reopen 在后(驳回)。
    """
    approve = _APPROVE_ACTION.get(user_feedback)
    if approve is None:
        return []  # 无反馈或非四种反馈 → 不在 review 流程
    approve_label = _APPROVE_LABEL[user_feedback]
    reject_label = _REJECT_LABEL[user_feedback]
    return [
        _action_info(approve, approve_label, remark_required=False),
        _action_info(TicketAction.REJECT_FOR_REOPEN, reject_label, remark_required=True),
    ]


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
    owner_name: str | None
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
    id: int | None = None             # 自增主键(只读,from_orm 灌入;to_orm 不写回)
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

    @property
    def delivery_status(self) -> str:
        """最近通知投递状态单值 — 快照委托。

        活动4态: pending/sent/failed/cancelled。
        none为列默认哨兵(历史遗留),读时经懒补全归一为pending。
        """
        return self._snapshot.delivery_status

    @property
    def last_notified_at(self) -> datetime | None:
        """最近一次成功通知时间 — 快照委托。

        首投/reminder 投递成功(sent)时刷新;失败/取消不动。
        """
        return self._snapshot.last_notified_at

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

    @property
    def feedback_verdict(self) -> str:
        """用户反馈 ⊗ 管理员 review 的成对裁决结果(读时派生委托纯函数,不落库)。

        委托模块级 :func:`compute_feedback_verdict`,输入既有 task_record 三字段
        (``user_feedback``/``review_decision``/``governance_status``)。在线自循环与
        离线侧复用同源规则。
        """
        return compute_feedback_verdict(
            self.user_feedback,
            self.review_decision,
            self.governance_status,
        )

    @property
    def available_actions(self) -> list[dict[str, Any]]:
        """该工单当前可做的 review 动作(按用户反馈派生,读时算不落库)。

        委托模块级 :func:`compute_available_actions`,按 ``user_feedback`` 返回对应
        同意+驳回动作。前端据此动态渲染,后端成动作单一事实源。非 review 流程
        (无反馈/非四种反馈)→ []。
        """
        return compute_available_actions(self.user_feedback)

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
        # 对齐 repo accept_feedback L190:离开 open 态时清 remind_at,
        # 避免残留 remind_at 触发 stale 提醒(TC-37)。review §LOW。
        self.remind_at = None

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

        逐字段对齐 repo ``close_ticket`` L226-237:
        governance_status='closed' / close_reason / closed_at / remind_at=None /
        cooldown_until(仅当传入) / active_worker=None(closed 释放)。
        """
        self.transition_to(GovernanceStatus.CLOSED)
        self.close_reason = close_reason
        self.closed_at = closed_at
        self.cooldown_until = cooldown_until
        self.assignee = None  # closed 释放 active_worker
        self.remind_at = None  # 对齐 repo L229,默认 None 清空

    def enter_observed(self, *, close_reason: str | None = None) -> None:
        """进入白名单观察态(OBSERVED)。

        与 :meth:`close` 的关键差异:转 OBSERVED 而非 CLOSED、**不设
        ``closed_at``(OBSERVED 非关闭,设了会被 ``find_latest_closed_by_worker``
        误纳 cooldown 视野)、释放 ``active_worker``(观察不占治理人力)、
        清 ``remind_at``。

        本方法是"转 OBSERVED"状态机动作的**单一入口** —— 状态机动作(转态+
        释放 assignee+清 remind_at+不设 closed_at+不碰 cooldown)是主职责,
        ``close_reason`` 是附带语义:关单转态场景传值,建单场景传 None。

        三路入口复用本方法(方案 A 链路同源):
          - ``review(approve_whitelist)`` 审批加白 → 传 WHITELIST_APPROVED
          - ``observe_for_whitelist`` scan 兜底关残留活跃单 → 传 SCAN_WHITELISTED
          - ``open_observed_ticket`` off-batch 建观察单(非关单)→ 不传(None)

        Args:
            close_reason: 观察来源(WHITELIST_APPROVED / SCAN_WHITELISTED);
                建单场景传 None(非关单,无关单原因)。
        """
        self.transition_to(GovernanceStatus.OBSERVED)
        self.close_reason = close_reason
        self.assignee = None  # 释放 active_worker(观察不占治理人力)
        self.remind_at = None

    def pause(self, *, review_reason: str) -> None:
        """暂停工单 — 进入 waiting_review。

        Args:
            review_reason: 暂停原因(admin_paused/schedule_due/...)。

        逐字段对齐 repo ``pause_ticket`` L270-272:
        governance_status='waiting_review' / review_reason / remind_at=None。
        """
        self.transition_to(GovernanceStatus.WAITING_REVIEW)
        self.review_reason = review_reason
        self.remind_at = None  # 对齐 repo L272,默认 None 清空

    def review(
        self,
        *,
        review_decision: str,
        reviewed_by: str,
        reviewed_at: datetime | None = None,
        review_remark: str | None = None,
        close_reason: str | None = None,
        cooldown_until: datetime | None = None,
    ) -> None:
        """管理员审核 — WAITING_REVIEW → CLOSED/SCHEDULED(四态分支)。

        Args:
            review_decision: 审核决策(approve_close / approve_scheduled /
                approve_whitelist / reject_for_reopen)。
            reviewed_by: 审核人 ID。
            reviewed_at: 审核时间(None → 取 now)。
            review_remark: 审核备注。
            close_reason: 关闭原因(None 时按 review_decision 取默认)。
            cooldown_until: 冷却截止时间(仅 approve_close 可带)。

        分支:
          approve_close     → CLOSED,close_reason=close_reason|'approve_close',
                              可带 cooldown_until
          approve_scheduled → SCHEDULED(同意排期,不关单),close_reason='schedule_approved',
                              保留 repair_deadline;不释放 active_worker(仍 active 观察)
          approve_whitelist → OBSERVED,close_reason=WHITELIST_APPROVED(白名单观察态:
                              释放 active_worker、不设 closed_at,由后续 off-batch
                              持续刷新快照,不发通知、不占治理人力)
          reject_for_reopen → CLOSED,close_reason='review_rejected'(打回仍关闭,
                              下个 scan 重建 open 单)
        """
        now = datetime.now()
        self.review_decision = review_decision
        self.reviewed_by = reviewed_by
        self.reviewed_at = reviewed_at or now
        self.review_remark = review_remark
        self.remind_at = None  # 无条件清

        if review_decision == "approve_scheduled":
            # 同意排期 → SCHEDULED(不关单,继续排期观察),不释放 active_worker
            self.transition_to(GovernanceStatus.SCHEDULED)
            self.close_reason = close_reason or "schedule_approved"
            return

        if review_decision == "approve_whitelist":
            # 加白 → OBSERVED(白名单观察态):释放 active_worker、不设 closed_at、
            # 清 remind_at(详见 enter_observed)。后续 off-batch 持续刷新快照,
            # 不发通知、不占治理人力。
            self.enter_observed(
                close_reason=close_reason or CloseReason.WHITELIST_APPROVED,
            )
            return

        # 其余两态(approve_close / reject_for_reopen)→ CLOSED
        self.transition_to(GovernanceStatus.CLOSED)
        self.closed_at = now
        self.assignee = None   # 释放 active_worker
        if review_decision == "approve_close":
            self.close_reason = close_reason or "approve_close"
            if cooldown_until is not None:
                self.cooldown_until = cooldown_until
        elif review_decision == "reject_for_reopen":
            self.close_reason = close_reason or "review_rejected"
        else:
            self.close_reason = close_reason or review_decision

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

    def update_token_baseline(self, value: int | None) -> None:
        """覆盖 guard 更新 token_baseline — 仅当传入非 None 时刷新。

        与 :meth:`refresh_snapshot` 的差异:refresh_snapshot 走 ``replace`` 会把
        ``token_baseline=None`` 无条件 erase 既有值;该方法 guard 非 None 才更新,
        保留既单(供 offline-batch 只在 有新基线数据 时覆盖,无则不动)。
        服务层应调本方法而非直戳 ``_snapshot``(DDD 封装)。

        Args:
            value: 新 token_baseline;None 时 no-op(保持既有)。
        """
        if value is None:
            return
        self._snapshot = replace(self._snapshot, token_baseline=value)

    # ── 工厂 ─────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        *,
        ticket_id: str | None,
        worker_id: str,
        bot_id: str | None,
        owner_id: str | None,
        owner_name: str | None,
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
            owner_name: 负责人显示名(展示用,可空)。
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
            owner_name=owner_name,
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
            owner_name=obj.owner_name,
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
                token_baseline=obj.token_baseline,
                task_summary=obj.task_summary,
                notification_structured=obj.notification_structured,
                analysis_status=obj.analysis_status,
                consecutive_normal_days=obj.consecutive_normal_days or 0,
                last_decision_dt_version=obj.last_decision_dt_version,
                last_seen_at=obj.last_seen_at,
                last_sync_at=obj.last_sync_at,
                delivery_status=_normalize_delivery_status(getattr(obj, "delivery_status", None)),
                last_notified_at=getattr(obj, "last_notified_at", None),
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
            id=getattr(obj, "id", None),
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
        row.owner_name = self.owner_name
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
        row.token_baseline = s.token_baseline
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
        row.delivery_status = self.delivery_status
        row.last_notified_at = self.last_notified_at
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
        row.delivery_status = self.delivery_status
        row.last_notified_at = self.last_notified_at

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
            "owner_name": self.owner_name,
            "dt_version": s.dt_version,
            "governance_decision": s.initial_decision,
            "latest_decision": s.current_decision,
            "hit_dimensions": s.triggered_dimensions,
            "hit_dimensions_count": s.hit_dimensions_count,
            "governance_max_priority": s.severity,
            "expected_token_saving": s.estimated_saving_tokens,
            "token_baseline": s.token_baseline,
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
