"""领域模型 — GovernanceNotification 通知投递生命周期。

与 GovernanceTicket / WhitelistEntry 同级,按实体拆文件。
FrozenSnapshot / NOTIFY_TRANSITIONS / IllegalNotifyTransitionError 本文件内联;
_iso 共享工具在 ``base.py``;ORM 映射见 ``repositories/orm.py``;
repo 用 from_orm/to_orm/apply_to 做翻译边界。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from agentclaw.community.core.economy.governance.domain.base import _iso
from agentclaw.community.core.economy.governance.domain.enums import (
    NotifyStatus,
    NotifyType,
)


class IllegalNotifyTransitionError(ValueError):
    """通知状态非法转换。"""


# ── 冻结快照(创建时不可变) ────────────────────────────


@dataclass(frozen=True, slots=True)
class FrozenSnapshot:
    """创建时冻结,永不可变。frozen 使赋值即 FrozenInstanceError。

    对应 ORM 列:
      - dt_version          ← orm.dt_version
      - decision_at_create  ← orm.governance_decision
      - triggered_dimensions ← orm.hit_dimensions
      - hit_dimensions_count ← orm.hit_dimensions_count
      - severity            ← orm.governance_max_priority
      - estimated_saving_tokens ← orm.expected_token_saving
      - saving_ratio        ← orm.saving_ratio
      - notification_md     ← orm.notification_md
      - notification_structured ← orm.notification_structured
    """

    dt_version: str
    decision_at_create: str
    triggered_dimensions: str | None
    hit_dimensions_count: int | None
    severity: str | None
    estimated_saving_tokens: int | None
    saving_ratio: float | None
    notification_md: str | None
    notification_structured: str | None


# ── 状态机转换表(通知) ─────────────────────────
# 合法转换: {当前状态: {允许的目标状态集合}}
NOTIFY_TRANSITIONS: dict[NotifyStatus, frozenset[NotifyStatus]] = {
    NotifyStatus.PENDING: frozenset({NotifyStatus.SENDING, NotifyStatus.CANCELLED}),
    NotifyStatus.SENDING: frozenset({
        NotifyStatus.SENT, NotifyStatus.FAILED, NotifyStatus.PENDING,
    }),
    NotifyStatus.SENT: frozenset(),       # 终态
    NotifyStatus.FAILED: frozenset(),     # 终态
    NotifyStatus.CANCELLED: frozenset(),  # 终态
}


@dataclass(slots=True)
class GovernanceNotification:
    """通知事件领域模型 — service 层唯一接触的对象。

    属性命名对齐 ORM business property(非 Column 原名),
    让 service 从 ORM 迁移到 domain 时属性访问零改动。

    不变量:
      - 身份 + 快照 逻辑不可变(由工厂 create 一次性赋值)
      - 投递态 只能通过 mark_* 方法变更(状态机守卫)
      - 22 sealed 列不在本模型上 → 编译期不可接触
    """

    # ── 身份(创建时写入,逻辑不可变) ──────────────
    notification_id: str
    ticket_id: str | None
    bot_id: str
    bot_name: str | None
    owner_id: str
    worker_id: str
    _snapshot: FrozenSnapshot

    # ── 投递态(可变,但只能走状态机) ──────────────
    delivery_status: NotifyStatus       # ORM: notify_status
    channel: str                        # ORM: notify_channel
    notify_type: NotifyType
    notify_source: str
    send_attempt_count: int
    last_send_at: datetime | None
    last_send_error: str | None
    external_message_id: str | None
    sent_at: datetime | None

    # ── 快照访问(只读) ──────────────────────────────

    @property
    def snapshot(self) -> FrozenSnapshot:
        """frozen dataclass — 外部拿不到可变句柄。"""
        return self._snapshot

    # ── 业务 property(对齐 ORM 同名 property) ──────

    @property
    def dt_version(self) -> str:
        """数据版本标识 — 快照委托。"""
        return self._snapshot.dt_version

    @property
    def decision_at_create(self) -> str:
        """创建时冻结决策 — 快照委托。"""
        return self._snapshot.decision_at_create

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
    def notification_md(self) -> str | None:
        """Markdown 渲染正文 — 快照委托。"""
        return self._snapshot.notification_md

    @property
    def notification_structured(self) -> str | None:
        """原始 JSON 结构 — 快照委托。"""
        return self._snapshot.notification_structured

    @property
    def is_pending(self) -> bool:
        """通知是否待发送。"""
        return self.delivery_status == NotifyStatus.PENDING

    # ── 状态机行为 ──────────────────────────────────────

    def transition_to(self, target: NotifyStatus) -> None:
        """状态机白名单转换。

        Args:
            target: 目标状态。

        Raises:
            IllegalNotifyTransitionError: 转换不在 NOTIFY_TRANSITIONS 白名单中。
        """
        allowed = NOTIFY_TRANSITIONS.get(self.delivery_status, frozenset())
        if target not in allowed:
            raise IllegalNotifyTransitionError(
                f"{self.delivery_status.value} -> {target.value} not allowed"
            )
        self.delivery_status = target

    def mark_claimed(self, now: datetime) -> None:
        """Atomic claim: pending → sending。

        Args:
            now: 当前时间,写入 last_send_at。
        """
        self.transition_to(NotifyStatus.SENDING)
        self.send_attempt_count += 1
        self.last_send_at = now

    def mark_sent(self, external_message_id: str, sent_at: datetime) -> None:
        """sending → sent。

        Args:
            external_message_id: 外部消息 ID。
            sent_at: 发送成功时间。
        """
        self.transition_to(NotifyStatus.SENT)
        self.external_message_id = external_message_id
        self.sent_at = sent_at
        self.last_send_error = None

    def mark_failed(self, error: str, *, terminal: bool) -> None:
        """sending → failed(终态) 或 pending(重试)。

        Args:
            error: 错误信息。
            terminal: True 表示达到最大重试次数,转为终态 FAILED;
                      False 表示非终态失败,回退到 PENDING 重试。
        """
        if terminal:
            self.transition_to(NotifyStatus.FAILED)
        else:
            self.transition_to(NotifyStatus.PENDING)
        self.last_send_error = error

    def can_send(self) -> bool:
        """是否可发送(PENDING 状态)。"""
        return self.delivery_status == NotifyStatus.PENDING

    # ── 工厂 ─────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        *,
        notification_id: str,
        ticket_id: str | None,
        bot_id: str,
        bot_name: str | None,
        owner_id: str,
        worker_id: str,
        snapshot: FrozenSnapshot,
        notify_type: NotifyType,
        notify_source: str = "online_cron",
        channel: str = "markdown",
    ) -> GovernanceNotification:
        """领域构造:创建即冻结快照,投递态初值 PENDING。

        Args:
            notification_id: 通知唯一 ID。
            ticket_id: 关联工单 ID。
            bot_id: Bot ID。
            bot_name: Bot 名称。
            owner_id: 负责人 ID。
            worker_id: Worker ID (owner_id:bot_id)。
            snapshot: 冻结快照(创建时一次性写入)。
            notify_type: 通知类型(first_send/reminder)。
            notify_source: 通知来源(online_cron/offline_batch/manual)。
            channel: 投递渠道(markdown/tc_card)。

        Returns:
            初始化的领域模型实例,delivery_status=PENDING,send_attempt_count=0。
        """
        return cls(
            notification_id=notification_id,
            ticket_id=ticket_id,
            bot_id=bot_id,
            bot_name=bot_name,
            owner_id=owner_id,
            worker_id=worker_id,
            _snapshot=snapshot,
            delivery_status=NotifyStatus.PENDING,
            channel=channel,
            notify_type=notify_type,
            notify_source=notify_source,
            send_attempt_count=0,
            last_send_at=None,
            last_send_error=None,
            external_message_id=None,
            sent_at=None,
        )

    # ── 翻译边界 ─────────────────────────────────────────

    @classmethod
    def from_orm(cls, obj: object) -> GovernanceNotification:
        """读翻译:ORM → 领域模型。

        Args:
            obj: orm.GovernanceNotificationOrm 实例(ORM 对象)。

        Returns:
            领域模型实例。sealed 列不会被映射 — 物理不泄漏。
        """
        return cls(
            notification_id=obj.notification_id,
            ticket_id=obj.ticket_id,
            bot_id=obj.bot_id,
            bot_name=obj.bot_name,
            owner_id=obj.owner_id,
            worker_id=obj.worker_id,
            _snapshot=FrozenSnapshot(
                dt_version=obj.dt_version or "",
                decision_at_create=obj.governance_decision or "actionable",
                triggered_dimensions=obj.hit_dimensions,
                hit_dimensions_count=obj.hit_dimensions_count,
                severity=obj.governance_max_priority,
                estimated_saving_tokens=obj.expected_token_saving,
                saving_ratio=float(obj.saving_ratio) if obj.saving_ratio is not None else None,
                notification_md=obj.notification_md,
                notification_structured=obj.notification_structured,
            ),
            delivery_status=NotifyStatus(obj.notify_status or "pending"),
            channel=obj.notify_channel or "markdown",
            notify_type=NotifyType(obj.notify_type or "first_send"),
            notify_source=obj.notify_source or "offline_batch",
            send_attempt_count=obj.send_attempt_count or 0,
            last_send_at=obj.last_send_at,
            last_send_error=obj.last_send_error,
            external_message_id=obj.external_message_id,
            sent_at=obj.sent_at,
        )

    def to_orm(self, row: object | None = None) -> object:
        """写翻译:领域模型 → ORM。

        新建时传 row=None 会创建 ORM 对象;更新已有行传 row。
        sealed 列不在领域模型上,物理写不到 — 封印由结构保证。

        Args:
            row: 可选已有 ORM 行;None 时新建。

        Returns:
            ORM 对象(已赋值,可 s.add)。
        """
        from agentclaw.community.core.economy.governance.repositories.orm import (
            GovernanceNotificationOrm,
        )
        row = row or GovernanceNotificationOrm()
        # 身份
        row.notification_id = self.notification_id
        row.ticket_id = self.ticket_id
        row.bot_id = self.bot_id
        row.bot_name = self.bot_name
        row.owner_id = self.owner_id
        row.worker_id = self.worker_id
        # 冻结快照
        s = self._snapshot
        row.dt_version = s.dt_version
        row.governance_decision = s.decision_at_create
        row.hit_dimensions = s.triggered_dimensions
        row.hit_dimensions_count = s.hit_dimensions_count
        row.governance_max_priority = s.severity
        row.expected_token_saving = s.estimated_saving_tokens
        row.saving_ratio = s.saving_ratio
        row.notification_md = s.notification_md
        row.notification_structured = s.notification_structured
        # 投递态
        row.notify_status = self.delivery_status.value
        row.notify_channel = self.channel
        row.notify_type = self.notify_type.value
        row.notify_source = self.notify_source
        row.send_attempt_count = self.send_attempt_count
        row.last_send_at = self.last_send_at
        row.last_send_error = self.last_send_error
        row.external_message_id = self.external_message_id
        row.sent_at = self.sent_at
        # ⚠️ 22 sealed 列:领域模型没有,物理不写 → 封印由结构保证
        # 但是 governance_cycle_id 是 NOT NULL 约束,必须提供回退值
        if not getattr(row, "governance_cycle_id", None):
            row.governance_cycle_id = getattr(row, "ticket_id", None) or uuid4().hex
        return row

    def apply_to(self, row: object) -> None:
        """增量写翻译:只把可变投递态写回已有 ORM,不碰冻结快照/sealed。

        用于 update 场景:读取 ORM 行 → 修改领域模型 → apply_to 写回。

        Args:
            row: 已有 ORM 行(从 session 查出)。
        """
        row.notify_status = self.delivery_status.value
        row.notify_channel = self.channel
        row.send_attempt_count = self.send_attempt_count
        row.last_send_at = self.last_send_at
        row.last_send_error = self.last_send_error
        row.external_message_id = self.external_message_id
        row.sent_at = self.sent_at

    def to_dict(self) -> dict:
        """API 序列化 — router 直接 ``data=[t.to_dict() for t in items]``。

        字段名对齐 ORM 列名(API 契约,前端依赖);sealed 列
        (id/env/gmt_create/gmt_modified)不在领域模型上,不暴露。
        时间字段转 ISO 字符串以便 JSON 序列化。
        """
        s = self._snapshot
        return {
            "notification_id": self.notification_id,
            "ticket_id": self.ticket_id,
            "bot_id": self.bot_id,
            "bot_name": self.bot_name,
            "owner_id": self.owner_id,
            "worker_id": self.worker_id,
            "dt_version": s.dt_version,
            "governance_decision": s.decision_at_create,
            "hit_dimensions": s.triggered_dimensions,
            "hit_dimensions_count": s.hit_dimensions_count,
            "governance_max_priority": s.severity,
            "expected_token_saving": s.estimated_saving_tokens,
            "saving_ratio": s.saving_ratio,
            "notification_md": s.notification_md,
            "notification_structured": s.notification_structured,
            "notify_status": self.delivery_status.value,
            "notify_channel": self.channel,
            "notify_type": self.notify_type.value,
            "notify_source": self.notify_source,
            "send_attempt_count": self.send_attempt_count,
            "last_send_at": _iso(self.last_send_at),
            "last_send_error": self.last_send_error,
            "external_message_id": self.external_message_id,
            "sent_at": _iso(self.sent_at),
        }