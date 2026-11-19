"""运行、作业、操作、工作区与运行候选（06 §2.4–§2.8）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from avernet_genome.models import BotRef

from .budget import BudgetUsage
from .enums import EndReason, OperationKind, OperationStatus, RunStatus

__all__ = [
    "ClaimedJob",
    "KillSwitchState",
    "LeaseState",
    "Operation",
    "Page",
    "Run",
    "RunCandidate",
    "TriggerRecord",
    "Workspace",
]


@dataclass(frozen=True)
class TriggerRecord:
    kind: str
    fire_time: datetime | None = None
    event_id: str | None = None
    requested_by: str | None = None


@dataclass(frozen=True)
class Run:
    """一次绑定执行；运行 id 是调用方唯一的句柄，永不重新开始。"""

    id: str
    bot: BotRef
    binding_id: str
    idempotency_key: str
    trigger: TriggerRecord
    strategy: str
    strategy_version: str
    parent_ref: str
    parent_revision: str
    allowed_genes: list[str]
    verification_profile: str
    params: dict[str, Any]
    budget_max: dict[str, int | float | None]
    granted: list[str]
    agent_definitions: dict[str, str]
    status: RunStatus
    attempt: int
    max_attempts: int
    budget_used: BudgetUsage
    candidate_count: int
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    end_reason: EndReason | None = None
    summary: dict[str, Any] | None = None
    cancel_requested: bool = False

    @property
    def budget(self):
        from .budget import Budget

        return Budget(
            max_usd=float(self.budget_max.get("max_usd", 0.0)),
            max_wall_clock_s=int(self.budget_max.get("max_wall_clock_s", 0)),
            max_rollouts=self.budget_max.get("max_rollouts"),
            max_tokens=self.budget_max.get("max_tokens"),
        )

    @property
    def strategy_ref(self) -> str:
        return f"{self.strategy}@{self.strategy_version}"

    @property
    def terminal(self) -> bool:
        return RunStatus(self.status).terminal


@dataclass(frozen=True)
class Job:
    """运行的一次派发尝试（每次尝试一个作业）。"""

    id: str
    run_id: str
    attempt: int
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    fencing_token: str | None = None
    open: bool = True


@dataclass(frozen=True)
class ClaimedJob:
    job: Job
    run: Run


@dataclass(frozen=True)
class LeaseState:
    lease_expires_at: datetime
    cancelled: bool


@dataclass(frozen=True)
class Operation:
    """长时能力调用（智能体会话 / 训练评估）。"""

    id: str
    run_id: str
    kind: OperationKind
    idempotency_key: str
    status: OperationStatus = OperationStatus.QUEUED
    cost: BudgetUsage = field(default_factory=BudgetUsage)
    created_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


@dataclass(frozen=True)
class Workspace:
    """按 (run, key) 幂等的沙箱物化记录。"""

    id: str
    run_id: str
    key: str
    revision: str
    created_at: datetime


@dataclass(frozen=True)
class RunCandidate:
    """运行提交的候选及其判定状态缓存（§12 evolution_run_candidate）。"""

    run_id: str
    candidate_id: str
    revision: str
    seq: int
    verdict: str = "pending"


@dataclass(frozen=True)
class KillSwitchState:
    """紧急停止开关的组合视图（§11.3）。"""

    paused: bool = False
    frozen_bots: frozenset[tuple[str, str]] = frozenset()
    disabled_strategies: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Page:
    items: list[Any]
    total: int
    page: int
    page_size: int
