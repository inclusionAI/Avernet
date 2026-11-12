"""L5 Method 九台状态机 conformance 测试(plan Task 2)。

钉死:METHOD_MACHINE_REGISTRY 九键逐字;各机器状态词汇/迁移规则/初始状态
= spec §3.2/§4.1–4.4 YAML 逐字;治理类别映射 per spec §4.2 词汇;
guard_refs 一律空(授权断言在 G1 服务层,spec §8.1/§8.2)。
L5 机器遵 G4 声明式"风格"注册:复用 StateMachineDefinition 结构与
validate_definition 的校验纪律(词汇/上限/引用完整性/可达性豁免),
validate_method_machine 为 L5 家族的注册期自检入口。纯声明,无 IO。
"""

import dataclasses

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.method_state_machines import (
    METHOD_MACHINE_REGISTRY,
    METHOD_STATE_GOVERNANCE,
    validate_method_machine,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine import (
    StateMachineDefinition,
    resolve_transition,
)

EXPECTED_REGISTRY_KEYS = frozenset(
    {
        "method_binding",
        "single_selection",
        "group_member",
        "swarm_plan",
        "swarm_stage",
        "swarm_allocation",
        "swarm_execution_record",
        "open_opportunity",
        "open_application",
    }
)

# 状态词汇逐字钉死(spec §3.2 / §4.1–4.4 YAML state 枚举)。
EXPECTED_STATE_VOCAB: dict[str, set[str]] = {
    "method_binding": {"BOUND", "ACTIVE", "SUSPENDED", "TERMINATED"},
    "single_selection": {"PROPOSED", "ACTIVE", "REPLACED", "RELEASED"},
    "group_member": {"INVITED", "ACTIVE", "SUSPENDED", "LEFT", "REMOVED", "REPLACED"},
    "swarm_plan": {"DRAFT", "READY", "RUNNING", "PAUSED", "COMPLETED", "FAILED", "CANCELLED"},
    "swarm_stage": {
        "PENDING", "READY", "ALLOCATING", "RUNNING", "SUCCEEDED", "FAILED", "SKIPPED", "CANCELLED",
    },
    "swarm_allocation": {"PROPOSED", "ACTIVE", "RELEASED", "EXPIRED", "REVOKED"},
    "swarm_execution_record": {"SUBMITTED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"},
    "open_opportunity": {"DRAFT", "OPEN", "PAUSED", "CLOSED", "CANCELLED"},
    "open_application": {"SUBMITTED", "SCREENING", "ADMITTED", "REJECTED", "WITHDRAWN", "REVOKED"},
}

EXPECTED_INITIAL_STATES = {
    "method_binding": "BOUND",
    "single_selection": "PROPOSED",
    "group_member": "INVITED",
    "swarm_plan": "DRAFT",
    "swarm_stage": "PENDING",
    "swarm_allocation": "PROPOSED",
    "swarm_execution_record": "SUBMITTED",
    "open_opportunity": "DRAFT",
    "open_application": "SUBMITTED",
}

# 迁移规则逐字钉死:(from_state, event_name, to_state) 集合(spec 操作族)。
EXPECTED_TRANSITIONS: dict[str, set[tuple[str, str, str]]] = {
    "method_binding": {
        ("BOUND", "method.activate", "ACTIVE"),
        ("ACTIVE", "method.suspend", "SUSPENDED"),
        ("SUSPENDED", "method.resume", "ACTIVE"),
        ("BOUND", "method.terminate", "TERMINATED"),
        ("ACTIVE", "method.terminate", "TERMINATED"),
        ("SUSPENDED", "method.terminate", "TERMINATED"),
    },
    "single_selection": {
        ("PROPOSED", "single.select", "ACTIVE"),
        ("ACTIVE", "single.replace", "REPLACED"),
        ("ACTIVE", "single.release", "RELEASED"),
    },
    "group_member": {
        ("INVITED", "group.add_member", "ACTIVE"),
        ("ACTIVE", "group.suspend_member", "SUSPENDED"),
        ("SUSPENDED", "group.resume_member", "ACTIVE"),
        ("ACTIVE", "group.leave", "LEFT"),
        ("SUSPENDED", "group.leave", "LEFT"),
        ("ACTIVE", "group.remove_member", "REMOVED"),
        ("SUSPENDED", "group.remove_member", "REMOVED"),
        ("ACTIVE", "group.replace_member", "REPLACED"),
    },
    "swarm_plan": {
        ("DRAFT", "swarm.plan.adopt", "READY"),
        ("READY", "swarm.plan.start", "RUNNING"),
        ("RUNNING", "swarm.plan.pause", "PAUSED"),
        ("PAUSED", "swarm.plan.resume", "RUNNING"),
        ("RUNNING", "swarm.plan.complete", "COMPLETED"),
        ("RUNNING", "swarm.plan.fail", "FAILED"),
        ("PAUSED", "swarm.plan.fail", "FAILED"),
        ("RUNNING", "swarm.plan.terminate", "CANCELLED"),
        ("PAUSED", "swarm.plan.terminate", "CANCELLED"),
    },
    "swarm_stage": {
        ("PENDING", "swarm.stage.ready", "READY"),
        ("READY", "swarm.stage.allocate", "ALLOCATING"),
        ("ALLOCATING", "swarm.stage.run", "RUNNING"),
        ("RUNNING", "swarm.stage.complete", "SUCCEEDED"),
        ("RUNNING", "swarm.stage.fail", "FAILED"),
        ("ALLOCATING", "swarm.stage.fail", "FAILED"),
        ("PENDING", "swarm.stage.skip", "SKIPPED"),
        ("READY", "swarm.stage.skip", "SKIPPED"),
        ("READY", "swarm.stage.cancel", "CANCELLED"),
        ("RUNNING", "swarm.stage.cancel", "CANCELLED"),
        ("ALLOCATING", "swarm.stage.cancel", "CANCELLED"),
    },
    "swarm_allocation": {
        ("PROPOSED", "swarm.allocation.activate", "ACTIVE"),
        ("ACTIVE", "swarm.allocation.release", "RELEASED"),
        ("ACTIVE", "swarm.allocation.expire", "EXPIRED"),
        ("ACTIVE", "swarm.allocation.revoke", "REVOKED"),
        ("PROPOSED", "swarm.allocation.revoke", "REVOKED"),
    },
    "swarm_execution_record": {
        ("SUBMITTED", "swarm.execution.start", "RUNNING"),
        ("RUNNING", "swarm.execution.succeed", "SUCCEEDED"),
        ("RUNNING", "swarm.execution.fail", "FAILED"),
        ("SUBMITTED", "swarm.execution.cancel", "CANCELLED"),
        ("RUNNING", "swarm.execution.cancel", "CANCELLED"),
    },
    "open_opportunity": {
        ("DRAFT", "open.opportunity.publish", "OPEN"),
        ("OPEN", "open.opportunity.pause", "PAUSED"),
        ("PAUSED", "open.opportunity.reopen", "OPEN"),
        ("OPEN", "open.opportunity.close", "CLOSED"),
        ("DRAFT", "open.opportunity.cancel", "CANCELLED"),
        ("OPEN", "open.opportunity.cancel", "CANCELLED"),
        ("PAUSED", "open.opportunity.cancel", "CANCELLED"),
    },
    "open_application": {
        ("SUBMITTED", "open.application.screen", "SCREENING"),
        ("SCREENING", "open.application.admit", "ADMITTED"),
        ("SUBMITTED", "open.application.reject", "REJECTED"),
        ("SCREENING", "open.application.reject", "REJECTED"),
        ("SUBMITTED", "open.application.withdraw", "WITHDRAWN"),
        ("ADMITTED", "open.application.revoke", "REVOKED"),
    },
}

# 治理类别映射逐字钉死(spec §4.2 七类;模块 docstring 同款)。
EXPECTED_STATE_GOVERNANCE = {
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
    # ADMITTED 归 active 而非 brief 压缩清单的 terminal_success:spec §4.4
    # `RevokeAdmission`(ADMITTED→REVOKED)要求出边,而 G4 纪律终态无出边
    # ——spec 操作族优先(作者裁决,与模块 docstring 一致)。
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
    # TERMINATED 未列在简写映射清单(brief 压缩遗漏):显式 terminate 属取消
    # 语义(与 L4 task.cancelled→terminal_cancelled 同族),作者裁决归
    # terminal_cancelled;三条 terminal_* 的行为均为"无出边 + 可达性必达"。
    "TERMINATED": "terminal_cancelled",
    "CANCELLED": "terminal_cancelled",
    "SKIPPED": "terminal_cancelled",
    "EXPIRED": "terminal_cancelled",
    "WITHDRAWN": "terminal_cancelled",
    "REVOKED": "terminal_cancelled",
    "FAILED": "terminal_failed",
    "REJECTED": "terminal_failed",
}


# ---------------------------------------------------------------- 注册表骨架

def test_registry_has_exactly_nine_machines():
    assert set(METHOD_MACHINE_REGISTRY) == EXPECTED_REGISTRY_KEYS
    assert len(METHOD_MACHINE_REGISTRY) == 9


def test_all_method_machines_pass_g4_validation():
    """九台机器注册即过 L5 G4 conformance:注册期自检 + G4 结构复用。"""
    for machine in METHOD_MACHINE_REGISTRY.values():
        assert isinstance(machine, StateMachineDefinition)
        validate_method_machine(machine)  # 不抛即通过


def test_binding_lifecycle_machine_vocabulary():
    m = METHOD_MACHINE_REGISTRY["method_binding"]
    assert {s.name for s in m.states} == {"BOUND", "ACTIVE", "SUSPENDED", "TERMINATED"}


@pytest.mark.parametrize("machine_id", sorted(EXPECTED_STATE_VOCAB))
def test_machines_cover_all_enums(machine_id):
    machine = METHOD_MACHINE_REGISTRY[machine_id]
    assert {s.name for s in machine.states} == EXPECTED_STATE_VOCAB[machine_id]
    # 词表与治理映射表零漂移:每个声明的状态都有映射条目。
    for state in machine.states:
        assert state.governance_kind == METHOD_STATE_GOVERNANCE[state.name]


@pytest.mark.parametrize("machine_id", sorted(EXPECTED_INITIAL_STATES))
def test_initial_states_pinned(machine_id):
    assert METHOD_MACHINE_REGISTRY[machine_id].initial_state == \
        EXPECTED_INITIAL_STATES[machine_id]


def test_governance_mapping_pinned():
    assert METHOD_STATE_GOVERNANCE == EXPECTED_STATE_GOVERNANCE


@pytest.mark.parametrize("machine_id", sorted(EXPECTED_TRANSITIONS))
def test_transitions_pinned_per_machine(machine_id):
    machine = METHOD_MACHINE_REGISTRY[machine_id]
    assert {
        (rule.from_states[0], rule.event_name, rule.to_state)
        for rule in machine.transitions
    } == EXPECTED_TRANSITIONS[machine_id]


def test_all_guard_refs_empty():
    """守卫一律空:授权语义在 G1 服务层断言,机器不带 store 守卫。"""
    for machine in METHOD_MACHINE_REGISTRY.values():
        for rule in machine.transitions:
            assert rule.guard_refs == ()


# ---------------------------------------------------------------- G4 裁决复用

def test_method_machines_resolve_via_g4():
    """machines 可直接喂给 G4 ``resolve_transition``(同构裁决,守卫空集)。"""
    binding = METHOD_MACHINE_REGISTRY["method_binding"]
    assert resolve_transition(binding, "BOUND", "method.activate", {}) == "ACTIVE"
    # 不匹配的 (state, event) 返回 None:事件接纳、状态不变。
    assert resolve_transition(binding, "TERMINATED", "method.activate", {}) is None


# ------------------------------------------------------- validate_method_machine

def test_validator_rejects_wrong_schema_version():
    bad = dataclasses.replace(
        METHOD_MACHINE_REGISTRY["method_binding"],
        schema_version="method-state-machine/9",
    )
    with pytest.raises(ContextCollabValidationError, match="schema_version"):
        validate_method_machine(bad)


def test_validator_rejects_wrong_compatibility_policy():
    bad = dataclasses.replace(
        METHOD_MACHINE_REGISTRY["method_binding"],
        compatibility_policy="legacy_rewrite",
    )
    with pytest.raises(ContextCollabValidationError, match="compatibility_policy"):
        validate_method_machine(bad)


def test_validator_rejects_bad_profile_version():
    bad = dataclasses.replace(
        METHOD_MACHINE_REGISTRY["method_binding"], profile_version=0,
    )
    with pytest.raises(ContextCollabValidationError, match="profile_version"):
        validate_method_machine(bad)


def test_validator_rejects_state_outside_l5_vocabulary():
    binding = METHOD_MACHINE_REGISTRY["method_binding"]
    stranger = dataclasses.replace(
        binding.states[0], name="created",
    )
    bad = dataclasses.replace(binding, states=(stranger,) + binding.states[1:])
    with pytest.raises(ContextCollabValidationError, match="unknown L5 state name"):
        validate_method_machine(bad)


def test_validator_rejects_unknown_governance_kind():
    binding = METHOD_MACHINE_REGISTRY["method_binding"]
    drifting = dataclasses.replace(
        binding.states[0], governance_kind="queued",
    )
    bad = dataclasses.replace(binding, states=(drifting,) + binding.states[1:])
    with pytest.raises(ContextCollabValidationError, match="unknown governance_kind"):
        validate_method_machine(bad)


def test_validator_rejects_governance_drift_from_mapping():
    """状态在 L5 词表内但治理类别与全局映射表不一致 → 拒绝(防漂移自检)。"""
    binding = METHOD_MACHINE_REGISTRY["method_binding"]
    drifted = dataclasses.replace(
        binding.states[1], governance_kind="blocked",
    )
    bad = dataclasses.replace(binding, states=(binding.states[0], drifted) + binding.states[2:])
    with pytest.raises(ContextCollabValidationError, match="governance drift"):
        validate_method_machine(bad)


def test_validator_rejects_unknown_allowed_method():
    binding = METHOD_MACHINE_REGISTRY["method_binding"]
    carrier = dataclasses.replace(
        binding.states[0], allowed_methods=("Plan",),
    )
    bad = dataclasses.replace(binding, states=(carrier,) + binding.states[1:])
    with pytest.raises(ContextCollabValidationError, match="unknown method"):
        validate_method_machine(bad)


def test_validator_rejects_non_empty_guard_refs():
    """L5 机器 guard_refs 必须空:G1 授权断言不进机器守卫。"""
    binding = METHOD_MACHINE_REGISTRY["method_binding"]
    guarded = dataclasses.replace(
        binding.transitions[0], guard_refs=("actor_can_start_task",),
    )
    bad = dataclasses.replace(binding, transitions=(guarded,) + binding.transitions[1:])
    with pytest.raises(ContextCollabValidationError, match="guard_refs"):
        validate_method_machine(bad)


def test_validator_rejects_unregistered_event_name():
    binding = METHOD_MACHINE_REGISTRY["method_binding"]
    off_vocabulary = dataclasses.replace(
        binding.transitions[0], event_name="task.started",
    )
    bad = dataclasses.replace(
        binding, transitions=(off_vocabulary,) + binding.transitions[1:],
    )
    with pytest.raises(ContextCollabValidationError, match="unregistered event_name"):
        validate_method_machine(bad)


def test_validator_rejects_undefined_state_references():
    binding = METHOD_MACHINE_REGISTRY["method_binding"]
    bad_from = dataclasses.replace(
        binding, transitions=(
            dataclasses.replace(
                binding.transitions[0], from_states=("MOLTEN",),
            ),
        ) + binding.transitions[1:],
    )
    with pytest.raises(ContextCollabValidationError, match="undefined from_state"):
        validate_method_machine(bad_from)

    bad_to = dataclasses.replace(
        binding, transitions=(
            dataclasses.replace(binding.transitions[0], to_state="MOLTEN"),
        ) + binding.transitions[1:],
    )
    with pytest.raises(ContextCollabValidationError, match="undefined to_state"):
        validate_method_machine(bad_to)


def test_validator_rejects_duplicate_rule_id():
    binding = METHOD_MACHINE_REGISTRY["method_binding"]
    duplicated = dataclasses.replace(
        binding, transitions=(binding.transitions[0], binding.transitions[0]),
    )
    with pytest.raises(ContextCollabValidationError, match="duplicate rule_id"):
        validate_method_machine(duplicated)


def test_validator_rejects_ambiguous_rules():
    """同一事件 + 重叠 from_state 命中多条规则 → 裁决不明确,拒绝。"""
    binding = METHOD_MACHINE_REGISTRY["method_binding"]
    ambiguous = dataclasses.replace(
        binding, transitions=(
            binding.transitions[0],
            dataclasses.replace(
                binding.transitions[0],
                rule_id="activate_r2",
                from_states=("BOUND", "ACTIVE"),
                to_state="SUSPENDED",
            ),
        ) + binding.transitions[1:],
    )
    with pytest.raises(ContextCollabValidationError, match="ambiguous transition rules"):
        validate_method_machine(ambiguous)


def test_validator_rejects_terminal_outgoing_edge():
    binding = METHOD_MACHINE_REGISTRY["method_binding"]
    resurrecting = dataclasses.replace(
        binding.transitions[0],
        rule_id="resurrect", from_states=("TERMINATED",),
        event_name="method.resume", to_state="ACTIVE",
    )
    bad = dataclasses.replace(binding, transitions=(resurrecting,))
    with pytest.raises(ContextCollabValidationError, match="must not have outgoing"):
        validate_method_machine(bad)


def test_validator_rejects_unreachable_non_exempt_state():
    binding = METHOD_MACHINE_REGISTRY["method_binding"]
    # 只剩 BOUND→TERMINATED 一条边:ACTIVE(active,非豁免)从 BOUND 不可达
    # → 违反可达性(§4.4 第 2 条);SUSPENDED(blocked,豁免)不参与检查。
    orphaned = dataclasses.replace(
        binding, transitions=(binding.transitions[3],),
    )
    with pytest.raises(ContextCollabValidationError, match="unreachable"):
        validate_method_machine(orphaned)


def test_validator_rejects_unknown_initial_state():
    bad = dataclasses.replace(
        METHOD_MACHINE_REGISTRY["method_binding"], initial_state="MOLTEN",
    )
    with pytest.raises(ContextCollabValidationError, match="initial_state"):
        validate_method_machine(bad)
