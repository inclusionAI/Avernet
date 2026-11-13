"""L5 Method 九台声明式状态机 + 注册期 G4 风格 conformance 校验(spec §8.2)。

九台机器(键逐字):``method_binding`` / ``single_selection`` / ``group_member`` /
``swarm_plan`` / ``swarm_stage`` / ``swarm_allocation`` /
``swarm_execution_record`` / ``open_opportunity`` / ``open_application``,
一个 machine 对象绑定一个实体状态列(spec §3.2 / §4.1–4.4)。
状态词汇 = spec YAML state 枚举逐字;迁移规则 = spec 操作族。

**G4 机制复用(为什么不是逐字调用 ``state_machine.validate_definition``)**:
L5 机器复用 G4 的 ``StateMachineDefinition`` / ``StateDefinition`` /
``TransitionRule`` 结构、七个治理类别、节点/字符串上限与可达性豁免
(waiting/blocked,Ruling R3),并按 spec §8.2 以"``validate_definition``
风格"注册于 ``METHOD_MACHINE_REGISTRY``。但既有 ``validate_definition``
把 state name 钉死在 L4 ``TaskStatus`` 词汇、event_name 钉死在任务事件
注册表、guard_refs 强制非空——三处 pin 均属 L4 任务机器家族合同,L5
对象状态列(BOUND/PROPOSED/...)与 method 操作族事件不在此列。故本模块
提供 ``validate_method_machine``:同一条 G4 校验纪律,仅三处按 L5 语
汇特化——状态词汇换为 ``METHOD_STATE_GOVERNANCE`` 闭合表、事件词汇
换为 ``METHOD_EVENT_KINDS`` 注册表、guard_refs 反向要求**一律空**
(守卫语义在 G1 服务层断言,spec §8.1/§8.2,机器不依赖 store)。
``resolve_transition`` 同构裁决迁移:守卫空集可直接喂入。

治理类别映射(spec §4.2 七类,钉死于 ``METHOD_STATE_GOVERNANCE``):
waiting → BOUND / PROPOSED / INVITED / DRAFT / PENDING / SUBMITTED;
active → ACTIVE / READY / RUNNING / OPEN / ADMITTED;review → SCREENING /
ALLOCATING;blocked → SUSPENDED / PAUSED;
terminal_success → REPLACED / RELEASED / LEFT / REMOVED / COMPLETED /
SUCCEEDED / CLOSED;terminal_cancelled → CANCELLED / SKIPPED / EXPIRED /
WITHDRAWN / REVOKED / TERMINATED;terminal_failed → FAILED / REJECTED。
三处作者裁决(brief 压缩映射的落地修正):① TERMINATED brief 清单未列,
归 terminal_cancelled(同 task.cancelled 显式取消语义);② ADMITTED 虽在
brief 的 success 系清单中,但 spec §4.4 操作族含 ``RevokeAdmission``
(ADMITTED→REVOKED),G4 纪律终态无出边,故 ADMITTED 归 active——
录取是仍可撤销的活状态,其终态是 REVOKED;其余 success 系无出边不受影响。
"""

from __future__ import annotations

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine import (
    COMPATIBILITY_POLICY,
    GOVERNANCE_KINDS,
    SCHEMA_VERSION,
    StateDefinition,
    StateMachineDefinition,
    TransitionRule,
    # 三个 L4 无关的 G4 校验原语(字段/头/可达性)直接 import 复用,
    # 不做本地拷贝——拷贝是 G4 conformance 纪律与 L5 机器间的唯一漂移
    # 通道(fix round 1 裁决)。
    _check_str,
    _validate_header,
    _validate_reachability,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodKind,
)

# L5 机器的事件注册表(spec §11.4 词汇闭合原则的 L5 对应面):
# method.* / single.* / group.* / swarm.* / open.* 操作族小写 token,
# 一事件名一操作;同事件多 from_state 用多条规则(r1/r2/... 后缀)。
METHOD_EVENT_KINDS: frozenset[str] = frozenset(
    {
        # method binding 生命周期(spec §3.2)
        "method.activate",
        "method.suspend",
        "method.resume",
        "method.terminate",
        # Single Profile(spec §4.1)
        "single.select",
        "single.replace",
        "single.release",
        # Group Profile(spec §4.2)
        "group.add_member",
        "group.suspend_member",
        "group.resume_member",
        "group.leave",
        "group.remove_member",
        "group.replace_member",
        # Swarm Profile(spec §4.3)
        "swarm.plan.adopt",
        "swarm.plan.start",
        "swarm.plan.pause",
        "swarm.plan.resume",
        "swarm.plan.complete",
        "swarm.plan.fail",
        "swarm.plan.terminate",
        "swarm.stage.ready",
        "swarm.stage.allocate",
        "swarm.stage.run",
        "swarm.stage.complete",
        "swarm.stage.fail",
        "swarm.stage.skip",
        "swarm.stage.cancel",
        "swarm.allocation.activate",
        "swarm.allocation.release",
        "swarm.allocation.expire",
        "swarm.allocation.revoke",
        "swarm.execution.start",
        "swarm.execution.succeed",
        "swarm.execution.fail",
        "swarm.execution.cancel",
        # Open Profile(spec §4.4)
        "open.opportunity.publish",
        "open.opportunity.pause",
        "open.opportunity.reopen",
        "open.opportunity.close",
        "open.opportunity.cancel",
        "open.application.screen",
        "open.application.admit",
        "open.application.reject",
        "open.application.withdraw",
        "open.application.revoke",
    }
)

# L5 状态→治理类别映射(逐字,防漂移唯一来源;机器定义从这里取 kind)。
METHOD_STATE_GOVERNANCE: dict[str, str] = {
    "BOUND": "waiting",
    "PROPOSED": "waiting",
    "INVITED": "waiting",
    "DRAFT": "waiting",
    "PENDING": "waiting",
    "SUBMITTED": "waiting",
    "ACTIVE": "active",
    "READY": "active",
    "RUNNING": "active",
    "OPEN": "active",
    # ADMITTED 归 active 而非 terminal_success:spec §4.4 `RevokeAdmission`
    # (ADMITTED→REVOKED)要求出边,G4 纪律终态无出边——见模块 docstring。
    "ADMITTED": "active",
    "SCREENING": "review",
    "ALLOCATING": "review",
    "SUSPENDED": "blocked",
    "PAUSED": "blocked",
    "REPLACED": "terminal_success",
    "RELEASED": "terminal_success",
    "LEFT": "terminal_success",
    "REMOVED": "terminal_success",
    "COMPLETED": "terminal_success",
    "SUCCEEDED": "terminal_success",
    "CLOSED": "terminal_success",
    "CANCELLED": "terminal_cancelled",
    "SKIPPED": "terminal_cancelled",
    "EXPIRED": "terminal_cancelled",
    "WITHDRAWN": "terminal_cancelled",
    "REVOKED": "terminal_cancelled",
    "TERMINATED": "terminal_cancelled",
    "FAILED": "terminal_failed",
    "REJECTED": "terminal_failed",
}


def _validate_states(d: StateMachineDefinition) -> dict[str, StateDefinition]:
    """状态词汇闭合 + 治理类别 Vocabulary + Method 兼容(L5 特化三点之一)。"""
    known_methods = {kind.value for kind in MethodKind}
    states: dict[str, StateDefinition] = {}
    for state in d.states:
        _check_str(state.name, "state name")
        if state.name not in METHOD_STATE_GOVERNANCE:
            raise ContextCollabValidationError(
                f"unknown L5 state name: {state.name!r}"
            )
        if state.name in states:
            raise ContextCollabValidationError(
                f"duplicate state definition: {state.name!r}"
            )
        _check_str(state.governance_kind, f"state {state.name!r} governance_kind")
        if state.governance_kind not in GOVERNANCE_KINDS:
            raise ContextCollabValidationError(
                f"unknown governance_kind: {state.governance_kind!r} "
                f"on state {state.name!r}"
            )
        if state.governance_kind != METHOD_STATE_GOVERNANCE[state.name]:
            raise ContextCollabValidationError(
                f"governance drift on state {state.name!r}: machine declares "
                f"{state.governance_kind!r}, L5 vocabulary "
                f"{METHOD_STATE_GOVERNANCE[state.name]!r}"
            )
        if not isinstance(state.allowed_methods, tuple) or not state.allowed_methods:
            raise ContextCollabValidationError(
                f"state {state.name!r} must declare allowed_methods"
            )
        for method in state.allowed_methods:
            _check_str(method, f"state {state.name!r} allowed_methods entry")
            if method not in known_methods:
                raise ContextCollabValidationError(
                    f"state {state.name!r} declares unknown method: {method!r}"
                )
        states[state.name] = state
    return states


def _validate_transitions(
    d: StateMachineDefinition, states: dict[str, StateDefinition],
) -> None:
    """迁移规则:引用完整性、终态无出边、rule_id 唯一、裁决唯一、守卫恒空。"""
    rule_ids: set[str] = set()
    for rule in d.transitions:
        _check_str(rule.rule_id, "rule_id")
        if rule.rule_id in rule_ids:
            raise ContextCollabValidationError(f"duplicate rule_id: {rule.rule_id!r}")
        rule_ids.add(rule.rule_id)

        _check_str(rule.event_name, f"rule {rule.rule_id!r} event_name")
        if rule.event_name not in METHOD_EVENT_KINDS:
            raise ContextCollabValidationError(
                f"rule {rule.rule_id!r} references unregistered event_name: "
                f"{rule.event_name!r}"
            )
        if not isinstance(rule.from_states, tuple) or not rule.from_states:
            raise ContextCollabValidationError(
                f"rule {rule.rule_id!r} must declare from_states"
            )
        for state_name in rule.from_states:
            if state_name not in states:
                raise ContextCollabValidationError(
                    f"rule {rule.rule_id!r} references undefined from_state: "
                    f"{state_name!r}"
                )
        _check_str(rule.to_state, f"rule {rule.rule_id!r} to_state")
        if rule.to_state not in states:
            raise ContextCollabValidationError(
                f"rule {rule.rule_id!r} references undefined to_state: "
                f"{rule.to_state!r}"
            )
        # L5 特化之三(之二为事件词汇、之一为状态词汇):守卫一律空
        # ——授权断言在 G1 服务层(spec §8.1),机器守卫非空即词汇漂移。
        if rule.guard_refs != ():
            raise ContextCollabValidationError(
                f"rule {rule.rule_id!r} must declare empty guard_refs; "
                f"L5 method machines keep guards in the G1 service layer"
            )

        # 终态无出边(与 G4 同条款)。
        for state_name in rule.from_states:
            if states[state_name].governance_kind.startswith("terminal_"):
                raise ContextCollabValidationError(
                    f"terminal state {state_name!r} must not have outgoing "
                    f"edges (rule {rule.rule_id!r})"
                )

    # 同一 state+event 命中多条规则 → 裁决不明确,拒绝(G4 同条款)。
    for i, first in enumerate(d.transitions):
        for second in d.transitions[i + 1:]:
            if first.event_name != second.event_name:
                continue
            overlap = set(first.from_states) & set(second.from_states)
            if overlap:
                raise ContextCollabValidationError(
                    f"ambiguous transition rules {first.rule_id!r} and "
                    f"{second.rule_id!r} both match event "
                    f"{first.event_name!r} from states {sorted(overlap)}"
                )


def validate_method_machine(d: StateMachineDefinition) -> None:
    """L5 method 机器注册期 conformance 校验(G4 ``validate_definition`` 风格)。

    非法即抛 ``ContextCollabValidationError``;三处 L5 特化见模块 docstring
    (状态词汇 / 事件词汇 / 守卫恒空),其余纪律(head、方法兼容、终态无
    出边、rule_id 唯一、裁决唯一、可达性豁免)与 G4 逐条同款。
    """
    _validate_header(d)
    states = _validate_states(d)
    _check_str(d.initial_state, "initial_state")
    if d.initial_state not in states:
        raise ContextCollabValidationError(
            f"initial_state is not a defined state: {d.initial_state!r}"
        )
    _validate_transitions(d, states)
    _validate_reachability(d, states)


def _state(name: str, methods: tuple[str, ...]) -> StateDefinition:
    """按唯一映射表取治理类别,置信状态定义(dict 表驱动防漂移)。"""
    return StateDefinition(
        name=name, governance_kind=METHOD_STATE_GOVERNANCE[name],
        allowed_methods=methods,
    )


def _transition(rule_id: str, from_state: str,
                event: str, to_state: str) -> TransitionRule:
    """单 from_state 迁移规则;同事件多 from_state 由多条 r1/r2 规则表达。"""
    return TransitionRule(
        rule_id=rule_id, from_states=(from_state,), event_name=event,
        guard_refs=(), to_state=to_state,
    )


# 全部 L5 绑定可跨任何方法 kind(顶层投影 Single/Open + Group,
# 嵌套 bind Group/Open/Swarm),机器族共享该状态列。
_ALL_KINDS: tuple[str, ...] = (
    "Single", "Open + Group", "Group", "Open", "Swarm",
)

# ------------------------------------------------------- binding 生命周期(spec §3.2)

METHOD_BINDING_MACHINE = StateMachineDefinition(
    schema_version=SCHEMA_VERSION,
    profile_ref="method_binding",
    profile_version=1,
    compatibility_policy=COMPATIBILITY_POLICY,
    initial_state="BOUND",
    states=(
        _state("BOUND", _ALL_KINDS),
        _state("ACTIVE", _ALL_KINDS),
        _state("SUSPENDED", _ALL_KINDS),
        _state("TERMINATED", _ALL_KINDS),
    ),
    transitions=(
        _transition("activate", "BOUND", "method.activate", "ACTIVE"),
        _transition("suspend", "ACTIVE", "method.suspend", "SUSPENDED"),
        _transition("resume", "SUSPENDED", "method.resume", "ACTIVE"),
        _transition("terminate_r1", "BOUND", "method.terminate", "TERMINATED"),
        _transition("terminate_r2", "ACTIVE", "method.terminate", "TERMINATED"),
        _transition("terminate_r3", "SUSPENDED", "method.terminate", "TERMINATED"),
    ),
)

# ------------------------------------------------------------ Single(spec §4.1)

SINGLE_SELECTION_MACHINE = StateMachineDefinition(
    schema_version=SCHEMA_VERSION,
    profile_ref="single_selection",
    profile_version=1,
    compatibility_policy=COMPATIBILITY_POLICY,
    initial_state="PROPOSED",
    states=(
        _state("PROPOSED", ("Single",)),
        _state("ACTIVE", ("Single",)),
        _state("REPLACED", ("Single",)),
        _state("RELEASED", ("Single",)),
    ),
    transitions=(
        _transition("select", "PROPOSED", "single.select", "ACTIVE"),
        _transition("replace", "ACTIVE", "single.replace", "REPLACED"),
        _transition("release", "ACTIVE", "single.release", "RELEASED"),
    ),
)

# ------------------------------------------------------------- Group(spec §4.2)

GROUP_MEMBER_MACHINE = StateMachineDefinition(
    schema_version=SCHEMA_VERSION,
    profile_ref="group_member",
    profile_version=1,
    compatibility_policy=COMPATIBILITY_POLICY,
    initial_state="INVITED",
    states=(
        _state("INVITED", ("Group",)),
        _state("ACTIVE", ("Group",)),
        _state("SUSPENDED", ("Group",)),
        _state("LEFT", ("Group",)),
        _state("REMOVED", ("Group",)),
        _state("REPLACED", ("Group",)),
    ),
    transitions=(
        _transition("add_member", "INVITED", "group.add_member", "ACTIVE"),
        _transition("suspend_member", "ACTIVE", "group.suspend_member", "SUSPENDED"),
        _transition("resume_member", "SUSPENDED", "group.resume_member", "ACTIVE"),
        _transition("leave_r1", "ACTIVE", "group.leave", "LEFT"),
        _transition("leave_r2", "SUSPENDED", "group.leave", "LEFT"),
        _transition("remove_member_r1", "ACTIVE", "group.remove_member", "REMOVED"),
        _transition("remove_member_r2", "SUSPENDED", "group.remove_member", "REMOVED"),
        _transition("replace_member", "ACTIVE", "group.replace_member", "REPLACED"),
    ),
)

# ------------------------------------------------------------- Swarm(spec §4.3)

SWARM_PLAN_MACHINE = StateMachineDefinition(
    schema_version=SCHEMA_VERSION,
    profile_ref="swarm_plan",
    profile_version=1,
    compatibility_policy=COMPATIBILITY_POLICY,
    initial_state="DRAFT",
    states=(
        _state("DRAFT", ("Swarm",)),
        _state("READY", ("Swarm",)),
        _state("RUNNING", ("Swarm",)),
        _state("PAUSED", ("Swarm",)),
        _state("COMPLETED", ("Swarm",)),
        _state("FAILED", ("Swarm",)),
        _state("CANCELLED", ("Swarm",)),
    ),
    transitions=(
        _transition("adopt", "DRAFT", "swarm.plan.adopt", "READY"),
        _transition("start", "READY", "swarm.plan.start", "RUNNING"),
        _transition("pause", "RUNNING", "swarm.plan.pause", "PAUSED"),
        _transition("resume", "PAUSED", "swarm.plan.resume", "RUNNING"),
        _transition("complete", "RUNNING", "swarm.plan.complete", "COMPLETED"),
        _transition("fail_r1", "RUNNING", "swarm.plan.fail", "FAILED"),
        _transition("fail_r2", "PAUSED", "swarm.plan.fail", "FAILED"),
        _transition("terminate_r1", "RUNNING", "swarm.plan.terminate", "CANCELLED"),
        _transition("terminate_r2", "PAUSED", "swarm.plan.terminate", "CANCELLED"),
    ),
)

SWARM_STAGE_MACHINE = StateMachineDefinition(
    schema_version=SCHEMA_VERSION,
    profile_ref="swarm_stage",
    profile_version=1,
    compatibility_policy=COMPATIBILITY_POLICY,
    initial_state="PENDING",
    states=(
        _state("PENDING", ("Swarm",)),
        _state("READY", ("Swarm",)),
        _state("ALLOCATING", ("Swarm",)),
        _state("RUNNING", ("Swarm",)),
        _state("SUCCEEDED", ("Swarm",)),
        _state("FAILED", ("Swarm",)),
        _state("SKIPPED", ("Swarm",)),
        _state("CANCELLED", ("Swarm",)),
    ),
    transitions=(
        _transition("ready", "PENDING", "swarm.stage.ready", "READY"),
        _transition("allocate", "READY", "swarm.stage.allocate", "ALLOCATING"),
        _transition("run", "ALLOCATING", "swarm.stage.run", "RUNNING"),
        _transition("complete", "RUNNING", "swarm.stage.complete", "SUCCEEDED"),
        _transition("fail_r1", "RUNNING", "swarm.stage.fail", "FAILED"),
        _transition("fail_r2", "ALLOCATING", "swarm.stage.fail", "FAILED"),
        _transition("skip_r1", "PENDING", "swarm.stage.skip", "SKIPPED"),
        _transition("skip_r2", "READY", "swarm.stage.skip", "SKIPPED"),
        _transition("cancel_r1", "READY", "swarm.stage.cancel", "CANCELLED"),
        _transition("cancel_r2", "RUNNING", "swarm.stage.cancel", "CANCELLED"),
        _transition("cancel_r3", "ALLOCATING", "swarm.stage.cancel", "CANCELLED"),
    ),
)

SWARM_ALLOCATION_MACHINE = StateMachineDefinition(
    schema_version=SCHEMA_VERSION,
    profile_ref="swarm_allocation",
    profile_version=1,
    compatibility_policy=COMPATIBILITY_POLICY,
    initial_state="PROPOSED",
    states=(
        _state("PROPOSED", ("Swarm",)),
        _state("ACTIVE", ("Swarm",)),
        _state("RELEASED", ("Swarm",)),
        _state("EXPIRED", ("Swarm",)),
        _state("REVOKED", ("Swarm",)),
    ),
    transitions=(
        _transition("activate", "PROPOSED", "swarm.allocation.activate", "ACTIVE"),
        _transition("release", "ACTIVE", "swarm.allocation.release", "RELEASED"),
        _transition("expire", "ACTIVE", "swarm.allocation.expire", "EXPIRED"),
        _transition("revoke_r1", "ACTIVE", "swarm.allocation.revoke", "REVOKED"),
        _transition("revoke_r2", "PROPOSED", "swarm.allocation.revoke", "REVOKED"),
    ),
)

SWARM_EXECUTION_RECORD_MACHINE = StateMachineDefinition(
    schema_version=SCHEMA_VERSION,
    profile_ref="swarm_execution_record",
    profile_version=1,
    compatibility_policy=COMPATIBILITY_POLICY,
    initial_state="SUBMITTED",
    states=(
        _state("SUBMITTED", ("Swarm",)),
        _state("RUNNING", ("Swarm",)),
        _state("SUCCEEDED", ("Swarm",)),
        _state("FAILED", ("Swarm",)),
        _state("CANCELLED", ("Swarm",)),
    ),
    transitions=(
        _transition("start", "SUBMITTED", "swarm.execution.start", "RUNNING"),
        _transition("succeed", "RUNNING", "swarm.execution.succeed", "SUCCEEDED"),
        _transition("fail", "RUNNING", "swarm.execution.fail", "FAILED"),
        _transition("cancel_r1", "SUBMITTED", "swarm.execution.cancel", "CANCELLED"),
        _transition("cancel_r2", "RUNNING", "swarm.execution.cancel", "CANCELLED"),
    ),
)

# --------------------------------------------------------------- Open(spec §4.4)

OPEN_OPPORTUNITY_MACHINE = StateMachineDefinition(
    schema_version=SCHEMA_VERSION,
    profile_ref="open_opportunity",
    profile_version=1,
    compatibility_policy=COMPATIBILITY_POLICY,
    initial_state="DRAFT",
    states=(
        _state("DRAFT", ("Open",)),
        _state("OPEN", ("Open",)),
        _state("PAUSED", ("Open",)),
        _state("CLOSED", ("Open",)),
        _state("CANCELLED", ("Open",)),
    ),
    transitions=(
        _transition("publish", "DRAFT", "open.opportunity.publish", "OPEN"),
        _transition("pause", "OPEN", "open.opportunity.pause", "PAUSED"),
        _transition("reopen", "PAUSED", "open.opportunity.reopen", "OPEN"),
        _transition("close", "OPEN", "open.opportunity.close", "CLOSED"),
        _transition("cancel_r1", "DRAFT", "open.opportunity.cancel", "CANCELLED"),
        _transition("cancel_r2", "OPEN", "open.opportunity.cancel", "CANCELLED"),
        _transition("cancel_r3", "PAUSED", "open.opportunity.cancel", "CANCELLED"),
    ),
)

OPEN_APPLICATION_MACHINE = StateMachineDefinition(
    schema_version=SCHEMA_VERSION,
    profile_ref="open_application",
    profile_version=1,
    compatibility_policy=COMPATIBILITY_POLICY,
    initial_state="SUBMITTED",
    states=(
        _state("SUBMITTED", ("Open",)),
        _state("SCREENING", ("Open",)),
        _state("ADMITTED", ("Open",)),
        _state("REJECTED", ("Open",)),
        _state("WITHDRAWN", ("Open",)),
        _state("REVOKED", ("Open",)),
    ),
    transitions=(
        _transition("screen", "SUBMITTED", "open.application.screen", "SCREENING"),
        _transition("admit", "SCREENING", "open.application.admit", "ADMITTED"),
        _transition("reject_r1", "SUBMITTED", "open.application.reject", "REJECTED"),
        _transition("reject_r2", "SCREENING", "open.application.reject", "REJECTED"),
        _transition("withdraw", "SUBMITTED", "open.application.withdraw", "WITHDRAWN"),
        _transition("revoke", "ADMITTED", "open.application.revoke", "REVOKED"),
    ),
)

# L5 method 机器注册表:九键逐字(plan Task 2 / spec §8.2)。
METHOD_MACHINE_REGISTRY: dict[str, StateMachineDefinition] = {
    "method_binding": METHOD_BINDING_MACHINE,
    "single_selection": SINGLE_SELECTION_MACHINE,
    "group_member": GROUP_MEMBER_MACHINE,
    "swarm_plan": SWARM_PLAN_MACHINE,
    "swarm_stage": SWARM_STAGE_MACHINE,
    "swarm_allocation": SWARM_ALLOCATION_MACHINE,
    "swarm_execution_record": SWARM_EXECUTION_RECORD_MACHINE,
    "open_opportunity": OPEN_OPPORTUNITY_MACHINE,
    "open_application": OPEN_APPLICATION_MACHINE,
}

# 注册即过 conformance(与 ``state_machine`` 内置机器同款快速失败):
# 字面量漂移在导入期立即抛错,不留到运行时。
for _machine in METHOD_MACHINE_REGISTRY.values():
    validate_method_machine(_machine)
