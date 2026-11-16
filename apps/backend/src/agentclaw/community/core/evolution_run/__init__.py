"""进化运行服务（06-evolution-run.zh-CN）：绑定、运行、租约作业、操作、预算与沙箱。

本模块是 06 的 Python 核心：与传输无关的领域模型与服务；HTTP 交付适配器在 09。
单机存储用 SQLite（§12），其他环境的服务端存储替换 `EvolutionStore`。
"""

from __future__ import annotations

from .api import EvolutionApi, error_envelope
from .app import EvolutionRuntime, build_evolution_runtime
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
from .context import RunStrategyContext, build_run_context
from .host import InProcessHost
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
from .container_host import ContainerHost
from .job_protocol import JobChannel, JobTokenIssuer, JobUnauthorized, WorkerTransport
from .cron import CronSchedule, CronSyntaxError
from .sandbox import JobWorkerSandbox, JobWorkerSpec, SandboxSpecError, job_worker_spec
from .runner import EvolutionRunner
from .run_service import RunService
from .scheduler import FiringResult, TriggerScheduler
from .worker import RemoteCandidates, Worker
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
from .store import EvolutionStore

__all__ = [
    "Worker",
    "RemoteCandidates",
    "WorkerTransport",
    "JobUnauthorized",
    "JobTokenIssuer",
    "JobChannel",
    "job_worker_spec",
    "SandboxSpecError",
    "JobWorkerSpec",
    "JobWorkerSandbox",
    "ContainerHost",
    "EvolutionRuntime",
    "build_evolution_runtime",
    "TriggerScheduler",
    "FiringResult",
    "CronSyntaxError",
    "CronSchedule",
    "ALWAYS_GRANTED_PARTS",
    "EvolutionApi",
    "error_envelope",
    "Binding",
    "BindingBusy",
    "BindingCheckFailed",
    "BindingsService",
    "InProcessHost",
    "RunStrategyContext",
    "build_run_context",
    "BotBindings",
    "BotPolicy",
    "Budget",
    "BudgetExhausted",
    "BudgetService",
    "BudgetUsage",
    "CeilingExceeded",
    "ChargeSource",
    "ClaimedJob",
    "EndReason",
    "EventName",
    "EventTrigger",
    "EvolutionFrozen",
    "EvolutionRunError",
    "EvolutionStore",
    "GateCheck",
    "GateDecision",
    "GateOutcome",
    "IdempotencyKeyReused",
    "Job",
    "JobNotFound",
    "JobService",
    "LeaseState",
    "ManualTrigger",
    "Operation",
    "OperationKind",
    "OperationNotFound",
    "OperationService",
    "OperationStatus",
    "Page",
    "PreconditionFailed",
    "RiskCeiling",
    "Rollout",
    "Run",
    "RunAlreadyEnded",
    "RunCandidate",
    "RunNotFound",
    "RunService",
    "RunStatus",
    "ScheduleTrigger",
    "StaleFencingToken",
    "EvolutionRunner",
    "TriggerKind",
    "TriggerRecord",
    "VerdictStatus",
    "Workspace",
]
