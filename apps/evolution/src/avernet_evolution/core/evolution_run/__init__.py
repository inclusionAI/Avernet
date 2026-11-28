"""进化运行服务（06-evolution-run.zh-CN）：绑定、运行、租约作业、操作、预算与沙箱。

本模块是 06 的 Python 核心：与传输无关的领域模型与服务；HTTP 交付适配器在 09。
单机存储用 SQLite（§12），其他环境的服务端存储替换 `EvolutionStore`。
"""

from __future__ import annotations

from .api import EvolutionApi, error_envelope
from .bindings import (
    Binding,
    BotBindings,
    EventTrigger,
    ManualTrigger,
    Rollout,
    ScheduleTrigger,
)
from .bindings_service import BindingsService
from .budget import Budget, BudgetUsage
from .budget_service import BudgetService
from .container_host import ContainerHost
from .context import RunStrategyContext, build_run_context
from .cron import CronSchedule, CronSyntaxError
from .enums import (
    ChargeSource,
    EndReason,
    EventName,
    OperationKind,
    OperationStatus,
    RiskCeiling,
    RunStatus,
    TriggerKind,
)
from .errors import (
    BindingBusy,
    BindingCheckFailed,
    BudgetExhausted,
    CeilingExceeded,
    EvolutionFrozen,
    EvolutionRunError,
    IdempotencyKeyReused,
    JobNotFound,
    OperationNotFound,
    PreconditionFailed,
    RunAlreadyEnded,
    RunNotFound,
    StaleFencingToken,
)
from .host import InProcessHost
from .job_protocol import JobChannel, JobTokenIssuer, JobUnauthorized, WorkerTransport
from .job_service import JobService
from .models_helpers import ALWAYS_GRANTED_PARTS
from .operation_service import OperationService
from .ports import (
    BotPolicy,
    GateCheck,
    GateDecision,
    GateOutcome,
    VerdictStatus,
)
from .records import (
    ClaimedJob,
    Job,
    LeaseState,
    Operation,
    Page,
    Run,
    RunCandidate,
    TriggerRecord,
    Workspace,
)
from .run_service import RunService
from .runner import EvolutionRunner
from .sandbox import JobWorkerSandbox, JobWorkerSpec, SandboxSpecError, job_worker_spec
from .scheduler import FiringResult, TriggerScheduler
from .store import EvolutionStore
from .worker import RemoteCandidates, Worker

__all__ = [
    "ALWAYS_GRANTED_PARTS",
    "Binding",
    "BindingBusy",
    "BindingCheckFailed",
    "BindingsService",
    "BotBindings",
    "BotPolicy",
    "Budget",
    "BudgetExhausted",
    "BudgetService",
    "BudgetUsage",
    "CeilingExceeded",
    "ChargeSource",
    "ClaimedJob",
    "ContainerHost",
    "CronSchedule",
    "CronSyntaxError",
    "EndReason",
    "EventName",
    "EventTrigger",
    "EvolutionApi",
    "EvolutionFrozen",
    "EvolutionRunError",
    "EvolutionRunner",
    "EvolutionStore",
    "FiringResult",
    "GateCheck",
    "GateDecision",
    "GateOutcome",
    "IdempotencyKeyReused",
    "InProcessHost",
    "Job",
    "JobChannel",
    "JobNotFound",
    "JobService",
    "JobTokenIssuer",
    "JobUnauthorized",
    "JobWorkerSandbox",
    "JobWorkerSpec",
    "LeaseState",
    "ManualTrigger",
    "Operation",
    "OperationKind",
    "OperationNotFound",
    "OperationService",
    "OperationStatus",
    "Page",
    "PreconditionFailed",
    "RemoteCandidates",
    "RiskCeiling",
    "Rollout",
    "Run",
    "RunAlreadyEnded",
    "RunCandidate",
    "RunNotFound",
    "RunService",
    "RunStatus",
    "RunStrategyContext",
    "SandboxSpecError",
    "ScheduleTrigger",
    "StaleFencingToken",
    "TriggerKind",
    "TriggerRecord",
    "TriggerScheduler",
    "VerdictStatus",
    "Worker",
    "WorkerTransport",
    "Workspace",
    "build_run_context",
    "error_envelope",
    "job_worker_spec",
]
