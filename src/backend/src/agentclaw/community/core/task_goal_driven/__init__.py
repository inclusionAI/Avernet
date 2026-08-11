"""任务目标驱动执行框架 (Task Goal-Driven Execution Framework).

领域模型与模块实现的权威设计:
- spec:   specs/2026-08-09-task-goal-driven-execution-framework/spec.md
- design: specs/2026-08-09-task-goal-driven-execution-framework/design-review.md (v4)
- plan:   specs/2026-08-09-task-goal-driven-execution-framework/tasks.md

本包是**从零重新实现**的框架,不复用旧 ``core/task`` 失效实现。模块对应设计六模块:
- :mod:`models`      — 领域模型 + 支撑 DTO (P0)
- :mod:`graph_store` — TaskGraphStore,内部 SSOT 写网关 (P1)
- :mod:`engine`      — ExecutionEngine,TaskCenter 内部编排核 (P2)
- :mod:`planner`     — TaskPlanner 规划编排壳,委托 DecomposerPort (P3, v4.2)
- :mod:`optimizer`   — OptimizerRule 可插拔策略契约 (P3 支撑)
- :mod:`protocols`   — PlannerPort/DecomposerPort/DispatcherPort/BbsExecutorPort seam
- :mod:`dispatcher`  — TaskDispatcher + BotDiscoverPort (P4)
- :mod:`execution`   — TaskExecution,executors + 动态拉群 (P5)
- :mod:`harness`     — TaskHarness,旁路常驻 (P6)

核心编排 (方案 C, v4): ExecutionEngine 统一驱动反应式 loop
(传播 DONE → 深度闸门 → 步进 plan → dispatch → MISS 处理 → 不动点 → 终验)。

v4 关键决策:
- Status 5 态 (删 SPAWNING);"委托中" = 有分解子 (结构派生)。
- FAIL/MISS 同构 (model B): 针对该节点产子挂它下,不复位下游。
- MISS 信号 = ``extend_props.miss_events`` (append+consume)。
- ``cascade_rollback`` 仅人工 ``rollback_to_node``;自动 reroute 不调用。
- 可插拔策略: ``OptimizerRule`` 规则链,默认 GAP/搜推,composition root 装配。
"""
from agentclaw.community.core.task_goal_driven.engine import ExecutionEngine
from agentclaw.community.core.task_goal_driven.optimizer import (
    GapBasedPlanningRule,
    Optimizer,
    OptimizerRule,
    SearchBasedDispatchRule,
)
from agentclaw.community.core.task_goal_driven.planner import TaskPlanner
from agentclaw.community.core.task_goal_driven.protocols import (
    BbsExecutorPort,
    DecomposerPort,
    DispatcherPort,
    PlannerPort,
)

from agentclaw.community.core.task_goal_driven.graph_store import (
    GraphNotFoundError,
    InvalidScopeTag,
    InvalidStateTransition,
    NodeNotFoundError,
    TaskGraphStore,
    TaskGraphStoreError,
)

from agentclaw.community.core.task_goal_driven.models import (
    AcceptanceCriteria,
    AcceptanceResult,
    AcceptanceVerdict,
    CollabMode,
    Context,
    DispatchKind,
    DispatchOutcome,
    ExecutorResult,
    ExecutorStatus,
    FilterCondition,
    Goal,
    GroupFormation,
    Metadata,
    NodeOpResult,
    NodeQueryCriteria,
    NodeRuntimePatch,
    RunMode,
    RuntimeInfo,
    SLA,
    Scope,
    SearchResult,
    SearchOutcome,
    Status,
    TaskExecutionGraph,
    TaskInfo,
    TaskNode,
    TaskOpResult,
    TaskSpec,
)

__all__ = [
    "PlannerPort",
    "DecomposerPort",
    "DispatcherPort",
    "BbsExecutorPort",
    "TaskPlanner",
    "ExecutionEngine",
    "OptimizerRule",
    "Optimizer",
    "GapBasedPlanningRule",
    "SearchBasedDispatchRule",
    "GraphNotFoundError",
    "InvalidScopeTag",
    "InvalidStateTransition",
    "NodeNotFoundError",
    "TaskGraphStore",
    "TaskGraphStoreError",
    "AcceptanceCriteria",
    "AcceptanceResult",
    "AcceptanceVerdict",
    "CollabMode",
    "Context",
    "DispatchKind",
    "DispatchOutcome",
    "ExecutorResult",
    "ExecutorStatus",
    "FilterCondition",
    "Goal",
    "GroupFormation",
    "Metadata",
    "NodeOpResult",
    "NodeQueryCriteria",
    "NodeRuntimePatch",
    "RunMode",
    "RuntimeInfo",
    "SLA",
    "Scope",
    "SearchResult",
    "SearchOutcome",
    "Status",
    "TaskExecutionGraph",
    "TaskInfo",
    "TaskNode",
    "TaskOpResult",
    "TaskSpec",
]
