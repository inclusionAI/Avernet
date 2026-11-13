"""L4 Collet 声明式生命周期状态机 + 注册期 G4 风格 conformance 校验(spec §4)。

单台机器(键逐字):``collet_lifecycle``,绑定 Collet 聚合根的
``ColletState`` 状态列。状态词汇 = ``ColletState`` 大写机器词汇;
迁移规则 = spec §4 五条逐字:

```text
CREATED   --collet.activated-->  ACTIVE
ACTIVE    --collet.suspended-->  SUSPENDED
SUSPENDED --collet.resumed-->    ACTIVE
ACTIVE|SUSPENDED --collet.completed--> COMPLETED
COMPLETED --collet.archived-->   ARCHIVED
```

**G4 机制复用(为什么不是逐字调用 ``state_machine.validate_definition``)**:
Collet 机器复用 G4 的 ``StateMachineDefinition`` / ``StateDefinition`` /
``TransitionRule`` 结构、七个治理类别、节点/字符串上限与可达性豁免
(waiting/blocked,Ruling R3),并按 G4 "``validate_definition`` 风格"
注册于 ``COLLET_MACHINE_REGISTRY``。但既有 ``validate_definition``
把 state name 钉死在 L4 ``TaskStatus`` 小写任务词汇、event_name 钉死在
``schema.REGISTERED_EVENT_KINDS`` 任务事件注册表、guard_refs 强制
**非空**——三处 pin 均属 L4 task 机器家族合同,``ColletState`` 大写
词汇与 ``collet.*`` 事件族不在此列,且机器不依赖 store(守卫语义在
G1 服务层断言)。故本模块提供 ``validate_collet_machine``:同一条 G4
校验纪律,仅三处按 Collet 词汇特化——状态词汇换为
``COLLET_STATE_GOVERNANCE`` 闭合表、事件词汇换为
``COLLET_EVENT_KINDS`` 注册表、guard_refs 反向要求**一律空**。
``resolve_transition`` 同构裁决迁移:守卫空集可直接喂入。

治理类别映射(spec §4.2 七类,钉死于 ``COLLET_STATE_GOVERNANCE``):
CREATED→waiting;ACTIVE→active;SUSPENDED→blocked;ARCHIVED→
terminal_success。作者裁决一处:COMPLETED 归 **review** 而非
terminal_*——G4 纪律终态无出边,而 spec §4 要求 COMPLETED 保有
``collet.archived`` 出边(归档是 Collet 的显式终止动作,终态是
ARCHIVED);review 类别本就是"无终态约束的活状态",不违反 §4.2。
``collet.created`` 仅注册不配规则:创建即入口,事件流由
``ac_context_collab_event`` 承载,不进机器裁决。
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
    # 三个与状态词汇无关的 G4 校验原语(字段/头/可达性)直接 import 复用,
    # 不做本地拷贝——拷贝是 G4 conformance 纪律与 Collet 机器间的唯一
    # 漂移通道(同 fix round 1 裁决,见 method_state_machines)。
    _check_str,
    _validate_header,
    _validate_reachability,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    ColletState,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodKind,
)

# Collet 机器的事件注册表(spec §11.4 词汇闭合原则的 Collet 对应面):
# collet.* 小写 token,与 schema.REGISTERED_EVENT_KINDS 的 task.* /
# user_interaction.* 词汇互不相交。``collet.created`` 仅注册不配规则
# (创建即入口);同事件多 from_state 用多条规则(r1/r2 后缀)。
COLLET_EVENT_KINDS: frozenset[str] = frozenset(
    {
        # 入口事件:仅注册,不配迁移规则(spec §4)。
        "collet.created",
        # 生命周期事件(spec §4 五条)。
        "collet.activated",
        "collet.suspended",
        "collet.resumed",
        "collet.completed",
        "collet.archived",
    }
)

# Collet 状态→治理类别映射(逐字,防漂移唯一来源;机器定义从这里取 kind)。
COLLET_STATE_GOVERNANCE: dict[str, str] = {
    "CREATED": "waiting",
    "ACTIVE": "active",
    "SUSPENDED": "blocked",
    # COMPLETED 归 review 而非 terminal_*:spec 要求保住 collet.archived
    # 出边(G4 纪律终态无出边),见模块 docstring 作者裁决。
    "COMPLETED": "review",
    "ARCHIVED": "terminal_success",
}


def _validate_states(d: StateMachineDefinition) -> dict[str, StateDefinition]:
    """状态词汇闭合 + 治理类别 Vocabulary + Method 兼容(Collet 特化三点之一)。"""
    known_methods = {kind.value for kind in MethodKind}
    known_states = {s.value for s in ColletState}
    states: dict[str, StateDefinition] = {}
    for state in d.states:
        _check_str(state.name, "state name")
        if state.name not in known_states:
            raise ContextCollabValidationError(
                f"state name is not a ColletState value: {state.name!r}"
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
        if state.governance_kind != COLLET_STATE_GOVERNANCE[state.name]:
            raise ContextCollabValidationError(
                f"governance drift on state {state.name!r}: machine declares "
                f"{state.governance_kind!r}, Collet vocabulary "
                f"{COLLET_STATE_GOVERNANCE[state.name]!r}"
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
        if rule.event_name not in COLLET_EVENT_KINDS:
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
        # Collet 特化之三(之二为事件词汇、之一为状态词汇):守卫一律空
        # ——授权断言在 G1 服务层(spec §8.1),机器守卫非空即词汇漂移。
        if rule.guard_refs != ():
            raise ContextCollabValidationError(
                f"rule {rule.rule_id!r} must declare empty guard_refs; "
                f"Collet machines keep guards in the G1 service layer"
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


def validate_collet_machine(d: StateMachineDefinition) -> None:
    """Collet 机器注册期 conformance 校验(G4 ``validate_definition`` 风格)。

    非法即抛 ``ContextCollabValidationError``;三处 Collet 特化见模块
    docstring(状态词汇 / 事件词汇 / 守卫恒空),其余纪律(head、方法
    兼容、终态无出边、rule_id 唯一、裁决唯一、可达性豁免)与 G4
    逐条同款。
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
        name=name, governance_kind=COLLET_STATE_GOVERNANCE[name],
        allowed_methods=methods,
    )


def _transition(rule_id: str, from_state: str,
                event: str, to_state: str) -> TransitionRule:
    """单 from_state 迁移规则;同事件多 from_state 由多条 r1/r2 规则表达。"""
    return TransitionRule(
        rule_id=rule_id, from_states=(from_state,), event_name=event,
        guard_refs=(), to_state=to_state,
    )


# Collet 是聚合根,任何方法 kind 的 Collet 都走同一生命周期(顶层投影
# Single/Open + Group,嵌套绑定 Group/Open/Swarm),机器共享该状态列。
_ALL_KINDS: tuple[str, ...] = (
    "Single", "Open + Group", "Group", "Open", "Swarm",
)

# --------------------------------------------------- Collet 生命周期(spec §4)

COLLET_LIFECYCLE_MACHINE = StateMachineDefinition(
    schema_version=SCHEMA_VERSION,
    profile_ref="collet_lifecycle",
    profile_version=1,
    compatibility_policy=COMPATIBILITY_POLICY,
    initial_state="CREATED",
    states=(
        _state("CREATED", _ALL_KINDS),
        _state("ACTIVE", _ALL_KINDS),
        _state("SUSPENDED", _ALL_KINDS),
        _state("COMPLETED", _ALL_KINDS),
        _state("ARCHIVED", _ALL_KINDS),
    ),
    transitions=(
        _transition("activate", "CREATED", "collet.activated", "ACTIVE"),
        _transition("suspend", "ACTIVE", "collet.suspended", "SUSPENDED"),
        _transition("resume", "SUSPENDED", "collet.resumed", "ACTIVE"),
        _transition("complete_r1", "ACTIVE", "collet.completed", "COMPLETED"),
        _transition("complete_r2", "SUSPENDED", "collet.completed", "COMPLETED"),
        _transition("archive", "COMPLETED", "collet.archived", "ARCHIVED"),
    ),
)

# Collet 机器注册表:单键逐字。
COLLET_MACHINE_REGISTRY: dict[str, StateMachineDefinition] = {
    "collet_lifecycle": COLLET_LIFECYCLE_MACHINE,
}

# 注册即过 conformance(与 ``state_machine`` 内置机器同款快速失败):
# 字面量漂移在导入期立即抛错,不留到运行时。
for _machine in COLLET_MACHINE_REGISTRY.values():
    validate_collet_machine(_machine)
