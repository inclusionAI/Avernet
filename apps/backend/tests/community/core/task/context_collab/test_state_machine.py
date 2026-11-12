"""G4 状态机单测(Task 5):声明式定义校验(spec §4.4)+ 迁移裁决 + 守卫事实(§4.3)。

钉死:内置 team_todo / BBS help goal 状态机(状态词汇经 ready→created、
active→in_progress 重命名);blocked 无入边由可达性豁免进入;守卫返回明确
PASS/FAIL 事实,禁止静默失败。纯函数,无 IO。
"""

import dataclasses

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine import (
    GUARD_REGISTRY,
    GuardEvaluationInput,
    GuardOutcome,
    BBS_HELP_REQUEST_GOAL_MACHINE,
    MACHINE_REGISTRY,
    TEAM_TODO_EXECUTION_MACHINE,
    StateDefinition,
    TransitionRule,
    resolve_transition,
    validate_definition,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)

PASS_OUTCOME = GuardOutcome(verdict="PASS", reason="test stub pass")
FAIL_OUTCOME = GuardOutcome(verdict="FAIL", reason="test stub fail")

P_RESPONSIBLE = PrincipalRef(principal_type="human", principal_id="u-1")
P_OTHER = PrincipalRef(principal_type="human", principal_id="u-2")
A_EXECUTOR = AgentRef(agent_type="bot", agent_id="bot-1")
P_EXECUTOR_BOT = PrincipalRef(principal_type="bot", principal_id="bot_coding_001")
A_EXECUTOR_BOT = AgentRef(agent_type="bot", agent_id="bot_coding_001")


# ---------------------------------------------------------------- 内置 machine 词汇

def test_builtin_machine_pinned():
    """六状态 / 六迁移逐字钉死(全局约束重命名后词汇,guard_refs 保留)。"""
    assert MACHINE_REGISTRY == {
        "team_todo_execution": TEAM_TODO_EXECUTION_MACHINE,
        "bbs_help_request_goal": BBS_HELP_REQUEST_GOAL_MACHINE,
    }
    assert TEAM_TODO_EXECUTION_MACHINE.schema_version == "context-state-machine/1"
    assert TEAM_TODO_EXECUTION_MACHINE.profile_ref == "team_todo_execution"
    assert TEAM_TODO_EXECUTION_MACHINE.profile_version == 1
    assert TEAM_TODO_EXECUTION_MACHINE.compatibility_policy == "new_contexts_only"
    assert TEAM_TODO_EXECUTION_MACHINE.initial_state == "created"
    assert [
        (s.name, s.governance_kind, s.allowed_methods)
        for s in TEAM_TODO_EXECUTION_MACHINE.states
    ] == [
        ("created", "waiting", ("Single",)),
        ("in_progress", "active", ("Single",)),
        ("reviewing", "review", ("Single",)),
        ("blocked", "blocked", ("Single",)),
        ("done", "terminal_success", ("Single",)),
        ("cancelled", "terminal_cancelled", ("Single",)),
    ]
    assert [
        (
            t.rule_id,
            t.from_states,
            t.event_name,
            t.guard_refs,
            t.to_state,
        )
        for t in TEAM_TODO_EXECUTION_MACHINE.transitions
    ] == [
        ("start_task", ("created",), "task.started",
         ("actor_can_start_task",), "in_progress"),
        ("update_progress", ("in_progress",), "task.progress.updated",
         ("event_matches_task",), "in_progress"),
        ("submit_for_review", ("in_progress",), "task.submitted",
         ("required_artifacts_present",), "reviewing"),
        ("accept_task", ("reviewing",), "task.accepted",
         ("actor_is_responsible_principal",), "done"),
        ("reject_task", ("reviewing",), "task.rejected",
         ("actor_is_responsible_principal",), "in_progress"),
        ("cancel_task", ("created", "in_progress", "reviewing", "blocked"),
         "task.cancelled", ("actor_can_cancel",), "cancelled"),
    ]


def test_bbs_help_request_goal_machine_pinned():
    """BBS 求助(带目标)复用目标状态语义,但 L5 方法保持 Open+Group。"""
    assert BBS_HELP_REQUEST_GOAL_MACHINE.profile_ref == "bbs_help_request_goal"
    assert BBS_HELP_REQUEST_GOAL_MACHINE.initial_state == "created"
    assert all(
        state.allowed_methods == ("Open + Group",)
        for state in BBS_HELP_REQUEST_GOAL_MACHINE.states
    )
    assert [
        (rule.event_name, rule.from_states, rule.to_state)
        for rule in BBS_HELP_REQUEST_GOAL_MACHINE.transitions
    ] == [
        ("task.started", ("created",), "in_progress"),
        ("task.progress.updated", ("in_progress",), "in_progress"),
        ("task.submitted", ("in_progress",), "reviewing"),
        ("task.accepted", ("reviewing",), "done"),
        ("task.rejected", ("reviewing",), "in_progress"),
        ("task.cancelled", ("created", "in_progress", "reviewing", "blocked"), "cancelled"),
    ]
    validate_definition(BBS_HELP_REQUEST_GOAL_MACHINE)


def test_governance_kinds_within_vocab():
    """全部状态 governance_kind 落在 spec §4.2 七类别词汇内。"""
    vocab = {
        "waiting", "active", "review", "blocked",
        "terminal_success", "terminal_cancelled", "terminal_failed",
    }
    for state in TEAM_TODO_EXECUTION_MACHINE.states:
        assert state.governance_kind in vocab


# ---------------------------------------------------------------- §4.4 定义校验

def test_validate_builtin_passes_with_blocked_exemption():
    """blocked 无入边,但因 governance_kind=blocked 豁免可达性检查(§4.3)。"""
    validate_definition(TEAM_TODO_EXECUTION_MACHINE)  # 不抛即通过


def test_undefined_initial_state_rejected():
    bad = dataclasses.replace(TEAM_TODO_EXECUTION_MACHINE, initial_state="uploads")
    with pytest.raises(ContextCollabValidationError,
                       match="initial_state is not a defined state"):
        validate_definition(bad)


def test_unreachable_non_exempt_state_rejected():
    """可对照的反例:blocked 无入边,若其 governance_kind 不在豁免类别内即拒。"""
    stale = StateDefinition(
        name="blocked", governance_kind="active", allowed_methods=("Single",),
    )
    states = tuple(
        stale if s.name == "blocked" else s
        for s in TEAM_TODO_EXECUTION_MACHINE.states
    )
    bad = dataclasses.replace(TEAM_TODO_EXECUTION_MACHINE, states=states)
    with pytest.raises(ContextCollabValidationError,
                       match="is unreachable from initial state"):
        validate_definition(bad)


def test_duplicate_rule_id_rejected():
    dup = TransitionRule(
        rule_id="start_task",
        from_states=("created",),
        event_name="task.completed",
        guard_refs=("actor_can_start_task",),
        to_state="in_progress",
    )
    bad = dataclasses.replace(
        TEAM_TODO_EXECUTION_MACHINE,
        transitions=TEAM_TODO_EXECUTION_MACHINE.transitions + (dup,),
    )
    with pytest.raises(ContextCollabValidationError, match="duplicate rule_id"):
        validate_definition(bad)


def test_ambiguous_conflicting_rules_rejected():
    """同一 state/event 命中多条规则 → 裁决不明确即拒。"""
    conflict = TransitionRule(
        rule_id="start_task_conflict",
        from_states=("created",),
        event_name="task.started",
        guard_refs=("actor_can_start_task",),
        to_state="reviewing",
    )
    bad = dataclasses.replace(
        TEAM_TODO_EXECUTION_MACHINE,
        transitions=TEAM_TODO_EXECUTION_MACHINE.transitions + (conflict,),
    )
    with pytest.raises(ContextCollabValidationError, match="ambiguous transition rules"):
        validate_definition(bad)


def test_terminal_outgoing_edge_rejected():
    """terminal_* 状态不能有出边(spec §4.4 第 3 条)。"""
    reopen = TransitionRule(
        rule_id="reopen",
        from_states=("done",),
        event_name="task.accepted",
        guard_refs=("actor_is_responsible_principal",),
        to_state="reviewing",
    )
    bad = dataclasses.replace(
        TEAM_TODO_EXECUTION_MACHINE,
        transitions=TEAM_TODO_EXECUTION_MACHINE.transitions + (reopen,),
    )
    with pytest.raises(ContextCollabValidationError,
                       match="must not have outgoing edges"):
        validate_definition(bad)


def test_unknown_event_name_rejected():
    """event_name 必须来自标准事件注册表(spec §4.4 第 4 条)。"""
    stale = dataclasses.replace(
        TEAM_TODO_EXECUTION_MACHINE.transitions[0], event_name="artifact.delivered",
    )
    transitions = (
        (stale,) + TEAM_TODO_EXECUTION_MACHINE.transitions[1:]
    )
    bad = dataclasses.replace(TEAM_TODO_EXECUTION_MACHINE, transitions=transitions)
    with pytest.raises(ContextCollabValidationError,
                       match="unregistered event_name"):
        validate_definition(bad)


def test_undefined_state_reference_rejected():
    bad_rule = TransitionRule(
        rule_id="teleport",
        from_states=("created",),
        event_name="task.assigned",
        guard_refs=("actor_can_start_task",),
        to_state="uploads",
    )
    bad = dataclasses.replace(
        TEAM_TODO_EXECUTION_MACHINE,
        transitions=TEAM_TODO_EXECUTION_MACHINE.transitions + (bad_rule,),
    )
    with pytest.raises(ContextCollabValidationError, match="undefined to_state"):
        validate_definition(bad)


def test_unknown_guard_ref_rejected():
    bad_rule = TransitionRule(
        rule_id="mystery",
        from_states=("created",),
        event_name="task.assigned",
        guard_refs=("actor_can_never_do_anything",),
        to_state="in_progress",
    )
    bad = dataclasses.replace(
        TEAM_TODO_EXECUTION_MACHINE,
        transitions=TEAM_TODO_EXECUTION_MACHINE.transitions + (bad_rule,),
    )
    with pytest.raises(ContextCollabValidationError, match="unregistered guard"):
        validate_definition(bad)


def test_invalid_governance_kind_rejected():
    stale = StateDefinition(
        name="done", governance_kind="finished", allowed_methods=("Single",),
    )
    states = tuple(
        stale if s.name == "done" else s
        for s in TEAM_TODO_EXECUTION_MACHINE.states
    )
    bad = dataclasses.replace(TEAM_TODO_EXECUTION_MACHINE, states=states)
    with pytest.raises(ContextCollabValidationError, match="unknown governance_kind"):
        validate_definition(bad)


def test_invalid_allowed_method_rejected():
    stale = StateDefinition(
        name="created", governance_kind="waiting", allowed_methods=("swarm",),
    )
    states = tuple(
        stale if s.name == "created" else s
        for s in TEAM_TODO_EXECUTION_MACHINE.states
    )
    bad = dataclasses.replace(TEAM_TODO_EXECUTION_MACHINE, states=states)
    with pytest.raises(ContextCollabValidationError, match="declares unknown method"):
        validate_definition(bad)


def test_allowed_method_accepts_swarm():
    updated = StateDefinition(
        name="created", governance_kind="waiting", allowed_methods=("Swarm",),
    )
    states = tuple(
        updated if s.name == "created" else s
        for s in TEAM_TODO_EXECUTION_MACHINE.states
    )
    valid = dataclasses.replace(TEAM_TODO_EXECUTION_MACHINE, states=states)
    validate_definition(valid)  # Swarm 已是合法值,不抛即通过


# ---------------------------------------------------------------- 迁移裁决

def test_resolve_transition_happy_path():
    result = resolve_transition(
        TEAM_TODO_EXECUTION_MACHINE, "created", "task.started",
        {"actor_can_start_task": PASS_OUTCOME},
    )
    assert result == "in_progress"


def test_resolve_transition_no_rule_returns_none():
    """无规则命中返回 None:事件仍可接纳,状态不变。"""
    assert resolve_transition(
        TEAM_TODO_EXECUTION_MACHINE, "created", "user_interaction.posted", {},
    ) is None


def test_resolve_transition_terminal_has_no_outgoing():
    """done 是 terminal_*,任何事件都无出边可命中。"""
    assert resolve_transition(
        TEAM_TODO_EXECUTION_MACHINE, "done", "task.accepted", {},
    ) is None


def test_resolve_transition_guard_fail_raises():
    with pytest.raises(ContextCollabValidationError, match="rejected event"):
        resolve_transition(
            TEAM_TODO_EXECUTION_MACHINE, "reviewing", "task.accepted",
            {"actor_is_responsible_principal": FAIL_OUTCOME},
        )


def test_resolve_transition_missing_guard_result_raises():
    """守卫结果缺失:fail-closed,禁止假定通过(§4.3 禁止静默失败)。"""
    with pytest.raises(ContextCollabValidationError,
                       match="missing guard result"):
        resolve_transition(TEAM_TODO_EXECUTION_MACHINE, "created", "task.started", {})


def test_resolve_transition_blocked_guard_raises():
    """守卫 BLOCKED 同样显式拒绝(fail-closed),不许静默降级。"""
    blocked = GuardOutcome(verdict="BLOCKED", reason="executor unreachable")
    with pytest.raises(ContextCollabValidationError, match="blocked event"):
        resolve_transition(
            TEAM_TODO_EXECUTION_MACHINE, "created", "task.started",
            {"actor_can_start_task": blocked},
        )


def test_resolve_transition_ambiguous_matches_raise():
    conflict = TransitionRule(
        rule_id="start_task_conflict",
        from_states=("created",),
        event_name="task.started",
        guard_refs=("actor_can_start_task",),
        to_state="reviewing",
    )
    bad = dataclasses.replace(
        TEAM_TODO_EXECUTION_MACHINE,
        transitions=TEAM_TODO_EXECUTION_MACHINE.transitions + (conflict,),
    )
    with pytest.raises(ContextCollabValidationError,
                       match="ambiguous transition rules match"):
        resolve_transition(bad, "created", "task.started", {})


# ---------------------------------------------------------------- §4.3 守卫事实

def _input(actor=P_RESPONSIBLE, responsible=P_RESPONSIBLE, executor=None, payload=None):
    return GuardEvaluationInput(
        actor=actor, responsible=responsible, executor=executor,
        payload={} if payload is None else payload,
    )


def test_guard_actor_can_start_task():
    guard = GUARD_REGISTRY["actor_can_start_task"]
    assert guard(_input()).verdict == "PASS"
    outcome = guard(_input(actor=P_OTHER, executor=A_EXECUTOR))
    assert outcome.verdict == "FAIL"
    assert outcome.reason == "actor is neither responsible principal nor executor"


def test_guard_actor_can_start_task_executor_bot():
    """Ruling R4:actor 与 executor 结构类型不同,须按值比较 bot 型 principal
    的 principal_id 与 executor 的 agent_id(spec §6.2 / §6.3)。"""
    guard = GUARD_REGISTRY["actor_can_start_task"]
    assert guard(
        _input(actor=P_EXECUTOR_BOT, executor=A_EXECUTOR_BOT)
    ).verdict == "PASS"
    # agent_id 不同 → 不是执行者,显式 FAIL
    outcome = guard(
        _input(actor=P_EXECUTOR_BOT, executor=AgentRef("bot", "bot_other_001"))
    )
    assert outcome.verdict == "FAIL"
    assert outcome.reason == "actor is neither responsible principal nor executor"


def test_guard_actor_is_responsible_principal():
    guard = GUARD_REGISTRY["actor_is_responsible_principal"]
    assert guard(_input()).verdict == "PASS"
    outcome = guard(_input(actor=P_OTHER))
    assert outcome.verdict == "FAIL"


def test_guard_actor_can_cancel():
    guard = GUARD_REGISTRY["actor_can_cancel"]
    assert guard(_input()).verdict == "PASS"
    assert guard(_input(actor=P_OTHER)).verdict == "FAIL"


def test_guard_required_artifacts_present():
    guard = GUARD_REGISTRY["required_artifacts_present"]
    assert guard(_input(payload={"artifacts": [{"uri": "a"}]})).verdict == "PASS"
    for payload in ({"artifacts": []}, {}, {"artifacts": "not-a-list"}):
        outcome = guard(_input(payload=payload))
        assert outcome.verdict == "FAIL"
        assert outcome.reason == "no artifacts in payload"


def test_guard_event_matches_task():
    guard = GUARD_REGISTRY["event_matches_task"]
    assert guard(_input(payload={"progress": "50%"})).verdict == "PASS"
    outcome = guard(_input(payload={"task_id": "t-1"}))
    assert outcome.verdict == "FAIL"
    assert outcome.reason == "explicit task_id not supported in P1"


def test_guard_registry_covers_all_machine_guard_refs():
    referenced = {
        ref
        for rule in TEAM_TODO_EXECUTION_MACHINE.transitions
        for ref in rule.guard_refs
    }
    assert referenced <= set(GUARD_REGISTRY)
    assert set(GUARD_REGISTRY) == {
        "actor_can_start_task",
        "actor_is_responsible_principal",
        "actor_can_cancel",
        "required_artifacts_present",
        "event_matches_task",
    }
