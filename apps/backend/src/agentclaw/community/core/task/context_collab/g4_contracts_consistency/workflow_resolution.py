"""工作流状态裁决纯函数(task_commission spec §4.2/裁决 G:自 services 提升)。

原 ``services.context_collab_service._resolve_event_status`` 的逐字等价
搬入 G4 层——乙案产品(products/)禁依赖 services,状态裁决是 Work 族
产品的公共需求,提升后通用服务与产品共用同源,消除复制漂移。

语义:
- Task 为 ``None``(外部部分行)→ ``None``(无可裁决状态);
  服务只允许这类上下文接收非 Work 事件;
- ``workflow_kind`` 未命中 ``MACHINE_REGISTRY`` → 返回原 status(不变);
- 当前状态对该事件无规则命中 → 返回原 status(G4 通用合同:事件仍可
  接纳,状态不变;产品面的时序刚性由各自的时序门承担,例如共享 ``product_contract.validate_product_event``);
- 命中规则:守卫按 ``GuardEvaluationInput`` 值语义求值,任一 FAIL/BLOCKED
  由 ``resolve_transition`` 抛 ``ContextCollabValidationError`` 向外传。

``TASK_COMMISSION_PRECONDITIONS`` 仅为兼容导出,从机器迁移派生;
G4 机器是唯一迁移权威。L6 WorkflowProfile 选择是否严格拒绝越序控制事件,
通用与产品入口都经共享 G4 检查,不另建时序表。
"""

from __future__ import annotations

from agentclaw.community.core.task.context_collab.l4_context_primitives.task import Task

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine import (
    GUARD_REGISTRY,
    GuardEvaluationInput,
    MACHINE_REGISTRY,
    VERSIONED_MACHINE_REGISTRY,
    TASK_COMMISSION_GOAL_MACHINE,
    resolve_transition,
)


def resolve_workflow_status(
    *,
    workflow_kind: str | None,
    task: Task | None,
    event_kind: str,
    payload: dict,
    actor,
    workflow_version: int | None = None,
) -> str | None:
    """Adjudicate an identified L4 Task, never a query snapshot or create DTO."""
    machine = (
        MACHINE_REGISTRY.get(workflow_kind)
        if workflow_version is None
        else VERSIONED_MACHINE_REGISTRY.get((workflow_kind, workflow_version))
    )
    if workflow_version is not None and machine is None:
        raise ContextCollabValidationError("versioned workflow machine is unavailable")
    if task is None:
        # Existing partial rows intentionally have no Task state to adjudicate.
        return None
    current_status = task.status
    if machine is None:
        return current_status
    matched_rules = [
        rule
        for rule in machine.transitions
        if rule.event_name == event_kind and current_status in rule.from_states
    ]
    if not matched_rules:
        return current_status
    guard_input = GuardEvaluationInput(
        actor=actor,
        responsible=task.principal_ref,
        executor=task.executor_ref,
        payload=payload,
    )
    guard_results = {
        guard_ref: GUARD_REGISTRY[guard_ref](guard_input)
        for rule in matched_rules
        for guard_ref in rule.guard_refs
    }
    new_status = resolve_transition(machine, current_status, event_kind, guard_results)
    return current_status if new_status is None else new_status


# Compatibility export, derived from the authoritative machine, never hand-maintained.
TASK_COMMISSION_PRECONDITIONS: dict[str, set[str]] = {
    rule.event_name: set(rule.from_states)
    for rule in TASK_COMMISSION_GOAL_MACHINE.transitions
}
