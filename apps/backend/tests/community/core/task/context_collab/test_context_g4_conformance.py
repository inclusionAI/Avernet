"""L4 Collet 生命周期状态机 conformance 测试(plan Task 1)。

钉死:COLLET_MACHINE_REGISTRY 单键 ``collet_lifecycle``;状态词汇 =
``ColletState`` 大写机器词汇闭合;治理类别映射 waiting/active/blocked/
review/terminal_success;迁移规则 = spec §4 五条逐字(``collet.created``
仅注册不配规则);guard_refs 一律空;``collet.*`` token 不进
schema.REGISTERED_EVENT_KINDS。Collet 机器遵 G4 声明式"风格"注册:
复用 StateMachineDefinition 结构与 G4 校验原语,
validate_collet_machine 为 Collet 机器的注册期自检入口。纯声明,无 IO。
"""

from collections import deque

from agentclaw.community.core.task.context_collab.g4_contracts_consistency.schema import (
    REGISTERED_EVENT_KINDS,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.context_state_machines import (
    COLLET_EVENT_KINDS,
    COLLET_MACHINE_REGISTRY,
    COLLET_STATE_GOVERNANCE,
    validate_collet_machine,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine import (
    StateMachineDefinition,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    ColletState,
)

LIFECYCLE = COLLET_MACHINE_REGISTRY["collet_lifecycle"]


# ---------------------------------------------------------------- 注册表骨架

def test_all_collet_machines_pass_validation():
    """注册期自检:COLLET_MACHINE_REGISTRY 内每台机器都过
    ``validate_collet_machine``(不抛即通过),且守卫恒空。"""
    assert set(COLLET_MACHINE_REGISTRY) == {"collet_lifecycle"}
    for machine in COLLET_MACHINE_REGISTRY.values():
        assert isinstance(machine, StateMachineDefinition)
        validate_collet_machine(machine)
        for rule in machine.transitions:
            assert rule.guard_refs == ()


def test_lifecycle_state_vocabulary():
    """状态词汇 = ``ColletState`` 闭合;治理映射逐字钉死(spec §4)。"""
    assert {state.value for state in ColletState} == {
        "CREATED", "ACTIVE", "SUSPENDED", "COMPLETED", "ARCHIVED",
    }
    assert {state.name for state in LIFECYCLE.states} == {
        state.value for state in ColletState
    }
    assert LIFECYCLE.initial_state == "CREATED"
    assert COLLET_STATE_GOVERNANCE == {
        "CREATED": "waiting",
        "ACTIVE": "active",
        "SUSPENDED": "blocked",
        # COMPLETED 归 review 而非 terminal_*:终态无出边会砍掉
        # collet.archived 出边(作者裁决,见模块 docstring)。
        "COMPLETED": "review",
        "ARCHIVED": "terminal_success",
    }
    # 迁移规则五条逐字(spec §4);collet.created 仅注册不配规则。
    assert {
        (rule.from_states[0], rule.event_name, rule.to_state)
        for rule in LIFECYCLE.transitions
    } == {
        ("CREATED", "collet.activated", "ACTIVE"),
        ("ACTIVE", "collet.suspended", "SUSPENDED"),
        ("SUSPENDED", "collet.resumed", "ACTIVE"),
        ("ACTIVE", "collet.completed", "COMPLETED"),
        ("SUSPENDED", "collet.completed", "COMPLETED"),
        ("COMPLETED", "collet.archived", "ARCHIVED"),
    }
    assert COLLET_EVENT_KINDS == frozenset(
        {
            "collet.created",
            "collet.activated",
            "collet.suspended",
            "collet.resumed",
            "collet.completed",
            "collet.archived",
        }
    )
    assert all(
        rule.event_name != "collet.created"
        for rule in LIFECYCLE.transitions
    )


def test_completed_is_not_terminal_and_keeps_archive():
    """COMPLETED 治理类别非 terminal 且保有 ``collet.archived`` 出边;
    ARCHIVED 有 ``collet.archived`` 入边且无出边(终态纪律)。"""
    completed = next(s for s in LIFECYCLE.states if s.name == "COMPLETED")
    assert not completed.governance_kind.startswith("terminal_")
    assert completed.governance_kind == "review"
    assert {
        (rule.event_name, rule.to_state)
        for rule in LIFECYCLE.transitions
        if "COMPLETED" in rule.from_states
    } == {("collet.archived", "ARCHIVED")}
    archived = next(s for s in LIFECYCLE.states if s.name == "ARCHIVED")
    assert archived.governance_kind == "terminal_success"
    assert {
        rule.event_name
        for rule in LIFECYCLE.transitions
        if rule.to_state == "ARCHIVED"
    } == {"collet.archived"}
    assert all(
        "ARCHIVED" not in rule.from_states
        for rule in LIFECYCLE.transitions
    )


def test_created_is_entry_and_all_states_reachable():
    """CREATED 是入口(initial_state)且归 waiting(可达性豁免语义,
    Ruling R3);全状态从入口可达,注册期校验不因可达性抛错。"""
    assert LIFECYCLE.initial_state == "CREATED"
    created = next(s for s in LIFECYCLE.states if s.name == "CREATED")
    assert created.governance_kind == "waiting"
    reachable = {LIFECYCLE.initial_state}
    queue = deque([LIFECYCLE.initial_state])
    while queue:
        current = queue.popleft()
        for rule in LIFECYCLE.transitions:
            if current in rule.from_states and rule.to_state not in reachable:
                reachable.add(rule.to_state)
                queue.append(rule.to_state)
    assert reachable == {state.name for state in LIFECYCLE.states}
    validate_collet_machine(LIFECYCLE)


def test_collet_tokens_not_in_event_schema():
    """词汇隔离:``collet.*`` token 不进 G4 任务事件注册表(两套词汇
    各自闭合,互相渗透即 schema 漂移)。"""
    assert COLLET_EVENT_KINDS.isdisjoint(REGISTERED_EVENT_KINDS)
