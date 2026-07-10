"""领域模型 — 共享基础 + 实体 re-export。

历史:此模块曾容纳三个领域主体(GovernanceNotification /
GovernanceTicket / WhitelistEntry),~1085 行触发 R9 1000 行门禁。
现按实体拆到 ``notification.py`` / ``ticket.py`` / ``whitelist.py``,
本文件只保留**共享基础**(errors / `_iso` / 冻结快照 / 可变快照 /
状态机转换表),并 re-export 三个实体,使既有 ``from ...domain.domain
import X`` 调用方零改动。

ORM 映射见 repositories/orm.py; repo 用 from_orm/to_orm/apply_to
做翻译边界。

关键设计:
  - sealed 列(id/env)不在领域模型上 → service 物理接触不到;
    gmt_create/gmt_modified 对 GovernanceTicket 作为只读元信息保留
    (评审/展示用,GovernanceNotification / WhitelistEntry 仍全 sealed)
  - 冻结快照通过 frozen dataclass 组合实现不可变
  - 投递态/生命周期只能通过状态机方法变更,禁止直接赋值
  - 业务初值("pending"/"open")在 create() 工厂,不埋 ORM default
  - WhitelistEntry 整体 frozen — 白名单只有 add/delete,无 update 语义
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from agentclaw.community.core.economy.governance.domain.enums import (
    GovernanceStatus,
    NotifyStatus,
    NotifyType,
)


class IllegalNotifyTransitionError(ValueError):
    """通知状态非法转换。"""


class IllegalTicketTransitionError(ValueError):
    """工单状态非法转换。"""


def _iso(value: datetime | None) -> str | None:
    """Serialize a datetime to ISO 8601 for API responses (None passes through)."""
    return value.isoformat() if value is not None else None


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


# ── 状态机转换表(工单) ─────────────────────────

TICKET_TRANSITIONS: dict[GovernanceStatus, frozenset[GovernanceStatus]] = {
    GovernanceStatus.OPEN: frozenset({
        GovernanceStatus.SCHEDULED, GovernanceStatus.WAITING_REVIEW,
        GovernanceStatus.CLOSED,
    }),
    GovernanceStatus.SCHEDULED: frozenset({
        GovernanceStatus.WAITING_REVIEW, GovernanceStatus.CLOSED,
    }),
    GovernanceStatus.WAITING_REVIEW: frozenset({
        GovernanceStatus.OPEN, GovernanceStatus.SCHEDULED,
        GovernanceStatus.CLOSED,
    }),
    GovernanceStatus.CLOSED: frozenset(),
}


# ── 实体 re-export(按实体拆分后的模块) ────────────────────
# 保留 ``from ...domain.domain import GovernanceTicket`` 等既有调用方零改动。
# 新代码应直接从实体模块 import。
from agentclaw.community.core.economy.governance.domain.notification import (  # noqa: E402
    GovernanceNotification,
)
from agentclaw.community.core.economy.governance.domain.ticket import (  # noqa: E402
    GovernanceTicket,
)
from agentclaw.community.core.economy.governance.domain.whitelist import (  # noqa: E402
    WhitelistEntry,
)


__all__ = [
    "FrozenSnapshot",
    "MutableSnapshot",
    "NOTIFY_TRANSITIONS",
    "TICKET_TRANSITIONS",
    "IllegalNotifyTransitionError",
    "IllegalTicketTransitionError",
    "GovernanceNotification",
    "GovernanceTicket",
    "WhitelistEntry",
    "NotifyStatus",
    "NotifyType",
    "GovernanceStatus",
]