"""工作流状态裁决纯函数(task_commission spec §4.2/裁决 G:自 services 提升)。

原 ``services.context_collab_service._resolve_event_status`` 的逐字等价
搬入 G4 层——乙案产品(products/)禁依赖 services,状态裁决是 Work 族
产品的公共需求,提升后通用服务与产品共用同源,消除复制漂移。

语义(与原函数零漂移):
- 快照为 ``None``(外部部分行)→ ``None``(无可裁决,事件照常接纳);
- ``workflow_kind`` 未命中 ``MACHINE_REGISTRY`` → 返回原 status(不变);
- 当前状态对该事件无规则命中 → 返回原 status(G4 通用合同:事件仍可
  接纳,状态不变;产品面的时序刚性由各自的时序门承担,例如
  ``TASK_COMMISSION_PRECONDITIONS``);
- 命中规则:守卫按 ``GuardEvaluationInput`` 值语义求值,任一 FAIL/BLOCKED
  由 ``resolve_transition`` 抛 ``ContextCollabValidationError`` 向外传。

``TASK_COMMISSION_PRECONDITIONS`` 是任务代办的产品时序门唯一权威表
(裁决 J):六动作各自的允许前置状态集,越轨在产品编排里先于机器裁决
被拒(终态与非法时序不落事件)。
"""

from __future__ import annotations

from agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine import (
    GUARD_REGISTRY,
    GuardEvaluationInput,
    MACHINE_REGISTRY,
    resolve_transition,
)


def resolve_workflow_status(
    *,
    workflow_kind: str | None,
    executor_ref,
    snapshot,
    event_kind: str,
    payload: dict,
    actor,
) -> str | None:
    """G4 机器裁决事件的接纳状态;返回新 status(不变即返回原 status)。

    参数按值语义自备(不消费 wire 命令类型):``executor_ref`` 是创建参数
    的 ``AgentRef | None``,``snapshot`` 是 record 伴随的
    ``CurrentSnapshot | None``,``actor`` 为已认证调用的 ``PrincipalRef``。
    """
    if snapshot is None:
        # 可空快照(外部部分行):无状态可裁决,事件照常接纳。
        return None
    current_status = snapshot.status
    machine = MACHINE_REGISTRY.get(workflow_kind)
    if machine is None:
        return current_status
    matched_rules = [
        rule
        for rule in machine.transitions
        if rule.event_name == event_kind
        and current_status in rule.from_states
    ]
    if not matched_rules:
        return current_status
    guard_input = GuardEvaluationInput(
        actor=actor,
        responsible=snapshot.principal_ref,
        executor=executor_ref,
        payload=payload,
    )
    guard_results = {
        guard_ref: GUARD_REGISTRY[guard_ref](guard_input)
        for rule in matched_rules
        for guard_ref in rule.guard_refs
    }
    new_status = resolve_transition(
        machine, current_status, event_kind, guard_results
    )
    return current_status if new_status is None else new_status


# 任务代办产品时序门(裁决 J):动作事件 → 允许的前置快照状态集。
# 与 TASK_COMMISSION_GOAL_MACHINE 六迁移形状互补:机器裁决状态推进,
# 本表在产品编排里先把"越时序/终态后"的入事件拒下(错误词指明状态与
# 动作),让 G4"无规则命中=事件接纳"的开放合同在刚性流程产品面上收紧。
TASK_COMMISSION_PRECONDITIONS: dict[str, set[str]] = {
    "task.started": {"created"},
    "task.progress.updated": {"in_progress"},
    "task.submitted": {"in_progress"},
    "task.accepted": {"reviewing"},
    "task.rejected": {"reviewing"},
    "task.cancelled": {"created", "in_progress", "reviewing", "blocked"},
}
