"""G4 状态机:声明式定义 + Conformance 校验 + 守卫 + 内置 team_todo_execution profile。

属 G4 ``State Machine``,不是 L4 新增上下文类(spec §4)。全部为纯声明式内容
与纯谓词守卫:禁止脚本、内联 SQL、网络、文件访问或框架 import 进入 Core
(spec §4.3)。状态词汇已按全局约束重命名(ready→created、active→in_progress),
迁移规则的 rule_id / event_name / guard_refs 逐字保留 spec §4.1 YAML。

Task 10 的 append-event 用例通过 ``MACHINE_REGISTRY`` 取机器、用
``resolve_transition`` 裁决迁移;守卫结果由调用方求值后传入。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.schema import (
    REGISTERED_EVENT_KINDS,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    TaskStatus,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodKind,
)

SCHEMA_VERSION = "context-state-machine/1"
COMPATIBILITY_POLICY = "new_contexts_only"

# spec §4.2 状态治理类别(七个)。
GOVERNANCE_KINDS: frozenset[str] = frozenset(
    {
        "waiting",
        "active",
        "review",
        "blocked",
        "terminal_success",
        "terminal_cancelled",
        "terminal_failed",
    }
)

# 可达性豁免(pre-flight Ruling R3):waiting / blocked 状态由外部失败事实或
# 人工补充进入,允许无入边(spec §4.3),不参与"全状态可达"检查。
REACHABILITY_EXEMPT_KINDS: frozenset[str] = frozenset({"waiting", "blocked"})

# spec §4.4 第 7 条:节点数与字符串上限(未知字段由 frozen dataclass 在构造
# 层面排除,运行时校验聚焦上限与词汇检查)。
MAX_STATES = 32
MAX_TRANSITIONS = 128
MAX_GUARD_REFS = 8
MAX_STRING_LENGTH = 256


@dataclass(frozen=True)
class StateDefinition:
    """一个状态:name 是 ``TaskStatus`` 值,governance_kind 见 §4.2 词汇。"""

    name: str
    governance_kind: str
    allowed_methods: tuple[str, ...]


@dataclass(frozen=True)
class TransitionRule:
    """一条迁移规则;``event_name`` 必须在 ``REGISTERED_EVENT_KINDS``。"""

    rule_id: str
    from_states: tuple[str, ...]
    event_name: str
    guard_refs: tuple[str, ...]
    to_state: str


@dataclass(frozen=True)
class StateMachineDefinition:
    """``context-state-machine/1`` 的内存形态(spec §4.1 YAML 的结构类型)。"""

    schema_version: str
    profile_ref: str
    profile_version: int
    compatibility_policy: str
    initial_state: str
    states: tuple[StateDefinition, ...]
    transitions: tuple[TransitionRule, ...]


@dataclass(frozen=True)
class GuardOutcome:
    """守卫求值结果:明确 PASS / FAIL / BLOCKED 事实,禁止静默失败(§4.3)。"""

    verdict: str
    reason: str


@dataclass(frozen=True)
class GuardEvaluationInput:
    """守卫求值的纯输入;不含任何仓储句柄或 IO 能力。"""

    actor: PrincipalRef
    responsible: PrincipalRef
    executor: AgentRef | None
    payload: dict


GuardFunction = Callable[[GuardEvaluationInput], GuardOutcome]


def _check_str(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{field} must be a non-empty string, got: {value!r}"
        )
    if len(value) > MAX_STRING_LENGTH:
        raise ContextCollabValidationError(
            f"{field} exceeds the {MAX_STRING_LENGTH}-char limit"
        )


def _validate_header(d: StateMachineDefinition) -> None:
    """§4.4 第 7/9 条:schema 版本、节点数上限与兼容策略显式声明。"""
    _check_str(d.schema_version, "schema_version")
    if d.schema_version != SCHEMA_VERSION:
        raise ContextCollabValidationError(
            f"unsupported schema_version: {d.schema_version!r}"
        )
    _check_str(d.profile_ref, "profile_ref")
    if (
        not isinstance(d.profile_version, int)
        or isinstance(d.profile_version, bool)
        or d.profile_version < 1
    ):
        raise ContextCollabValidationError(
            f"profile_version must be a positive int, got: {d.profile_version!r}"
        )
    _check_str(d.compatibility_policy, "compatibility_policy")
    if d.compatibility_policy != COMPATIBILITY_POLICY:
        raise ContextCollabValidationError(
            f"unsupported compatibility_policy: {d.compatibility_policy!r}; "
            f"P1 only accepts {COMPATIBILITY_POLICY!r}"
        )
    if not isinstance(d.states, tuple) or not d.states:
        raise ContextCollabValidationError("states must be a non-empty tuple")
    if len(d.states) > MAX_STATES:
        raise ContextCollabValidationError(
            f"too many states: {len(d.states)} > {MAX_STATES}"
        )
    if not isinstance(d.transitions, tuple):
        raise ContextCollabValidationError("transitions must be a tuple")
    if len(d.transitions) > MAX_TRANSITIONS:
        raise ContextCollabValidationError(
            f"too many transitions: {len(d.transitions)} > {MAX_TRANSITIONS}"
        )


def _validate_states(d: StateMachineDefinition) -> dict[str, StateDefinition]:
    """§4.4 第 1/6/7 条:状态定义、治理类别词汇与 Method 兼容。"""
    known_methods = {kind.value for kind in MethodKind}
    states: dict[str, StateDefinition] = {}
    for state in d.states:
        _check_str(state.name, "state name")
        if state.name not in {status.value for status in TaskStatus}:
            raise ContextCollabValidationError(
                f"state name is not a TaskStatus value: {state.name!r}"
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
    d: StateMachineDefinition,
    states: dict[str, StateDefinition],
) -> None:
    """§4.4 第 3/4/5/6 条:引用完整性、终态无出边、rule_id 唯一与裁决唯一。"""
    rule_ids: set[str] = set()
    for rule in d.transitions:
        _check_str(rule.rule_id, "rule_id")
        if rule.rule_id in rule_ids:
            raise ContextCollabValidationError(f"duplicate rule_id: {rule.rule_id!r}")
        rule_ids.add(rule.rule_id)

        _check_str(rule.event_name, f"rule {rule.rule_id!r} event_name")
        if rule.event_name not in REGISTERED_EVENT_KINDS:
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
        if not isinstance(rule.guard_refs, tuple) or not rule.guard_refs:
            raise ContextCollabValidationError(
                f"rule {rule.rule_id!r} must declare guard_refs"
            )
        if len(rule.guard_refs) > MAX_GUARD_REFS:
            raise ContextCollabValidationError(
                f"rule {rule.rule_id!r} exceeds the {MAX_GUARD_REFS}-guard limit"
            )
        for guard_ref in rule.guard_refs:
            _check_str(guard_ref, f"rule {rule.rule_id!r} guard_ref")
            if guard_ref not in GUARD_REGISTRY:
                raise ContextCollabValidationError(
                    f"rule {rule.rule_id!r} references unregistered guard: "
                    f"{guard_ref!r}"
                )

        # 终态无出边(§4.4 第 3 条)放在引用检查之后、唯一性检查之前单独立住。
        for state_name in rule.from_states:
            if states[state_name].governance_kind.startswith("terminal_"):
                raise ContextCollabValidationError(
                    f"terminal state {state_name!r} must not have outgoing "
                    f"edges (rule {rule.rule_id!r})"
                )

    # 同一 state+event 命中多条规则 → 多 guard 裁决不明确,拒绝(§4.4 第 5 条)。
    for i, first in enumerate(d.transitions):
        for second in d.transitions[i + 1 :]:
            if first.event_name != second.event_name:
                continue
            overlap = set(first.from_states) & set(second.from_states)
            if overlap:
                raise ContextCollabValidationError(
                    f"ambiguous transition rules {first.rule_id!r} and "
                    f"{second.rule_id!r} both match event "
                    f"{first.event_name!r} from states {sorted(overlap)}"
                )


def _validate_reachability(
    d: StateMachineDefinition, states: dict[str, StateDefinition]
) -> None:
    """§4.4 第 2 条:非豁免状态必须从起点可达(waiting/blocked 例外,Ruling R3)。"""
    reachable: set[str] = {d.initial_state}
    queue: deque[str] = deque([d.initial_state])
    while queue:
        current = queue.popleft()
        for rule in d.transitions:
            if current in rule.from_states and rule.to_state not in reachable:
                reachable.add(rule.to_state)
                queue.append(rule.to_state)
    for state in states.values():
        if state.governance_kind in REACHABILITY_EXEMPT_KINDS:
            continue
        if state.name not in reachable:
            raise ContextCollabValidationError(
                f"state {state.name!r} is unreachable from initial state "
                f"{d.initial_state!r}"
            )


def validate_definition(d: StateMachineDefinition) -> None:
    """G4 Conformance 校验(spec §4.4 第 1-7 条),非法即抛
    ``ContextCollabValidationError``。注册或绑定状态机时必须调用。"""
    _validate_header(d)
    states = _validate_states(d)
    _check_str(d.initial_state, "initial_state")
    if d.initial_state not in states:
        raise ContextCollabValidationError(
            f"initial_state is not a defined state: {d.initial_state!r}"
        )
    _validate_transitions(d, states)
    _validate_reachability(d, states)


def resolve_transition(
    d: StateMachineDefinition,
    current_state: str,
    event_kind: str,
    guard_results: dict[str, GuardOutcome],
) -> str | None:
    """裁决 ``current_state`` + ``event_kind`` 的迁移结果。

    - 无规则命中返回 ``None``:事件仍可接纳,状态不变;
    - 多条规则同时命中抛 ``ContextCollabValidationError``(裁决不明确);
    - 命中规则的任一守卫 FAIL / BLOCKED 抛 ``ContextCollabValidationError``
      (拒绝事件,不静默降级);守卫结果缺失同样抛错,禁止假定通过。
    """
    matches = [
        rule
        for rule in d.transitions
        if rule.event_name == event_kind and current_state in rule.from_states
    ]
    if not matches:
        return None
    if len(matches) > 1:
        raise ContextCollabValidationError(
            "ambiguous transition rules match state "
            f"{current_state!r} on event {event_kind!r}: "
            + ", ".join(repr(rule.rule_id) for rule in matches)
        )
    rule = matches[0]
    for guard_ref in rule.guard_refs:
        outcome = guard_results.get(guard_ref)
        if not isinstance(outcome, GuardOutcome):
            raise ContextCollabValidationError(
                f"missing guard result for {guard_ref!r} on rule {rule.rule_id!r}"
            )
        if outcome.verdict == "PASS":
            continue
        if outcome.verdict not in ("PASS", "FAIL", "BLOCKED"):
            raise ContextCollabValidationError(
                f"unknown guard verdict: {outcome.verdict!r}"
            )
        action = "blocked" if outcome.verdict == "BLOCKED" else "rejected"
        raise ContextCollabValidationError(
            f"guard {guard_ref!r} {action} event {event_kind!r} on rule "
            f"{rule.rule_id!r}: {outcome.reason}"
        )
    return rule.to_state


# --------------------------------------------------------------------- 守卫(§4.3)


def _guard_actor_can_start_task(
    evaluation: GuardEvaluationInput,
) -> GuardOutcome:
    # actor 是北向 API 的 PrincipalRef(spec §6.3:人与 bot 同用 principal_type
    # + principal_id),executor 是 AgentRef(spec §6.2),两者结构类型不同,
    # 跨类型相等恒为 False。判"actor 即执行者"必须按值比较:bot 型 principal
    # 且 principal_id == executor.agent_id(Controller Ruling R4)。
    if evaluation.actor == evaluation.responsible:
        return GuardOutcome(verdict="PASS", reason="actor may start the task")
    executor = evaluation.executor
    if (
        executor is not None
        and evaluation.actor.principal_type == "bot"
        and evaluation.actor.principal_id == executor.agent_id
    ):
        return GuardOutcome(verdict="PASS", reason="actor is the executor agent")
    return GuardOutcome(
        verdict="FAIL",
        reason="actor is neither responsible principal nor executor",
    )


def _guard_actor_is_responsible_principal(
    evaluation: GuardEvaluationInput,
) -> GuardOutcome:
    if evaluation.actor == evaluation.responsible:
        return GuardOutcome(verdict="PASS", reason="actor is the responsible principal")
    return GuardOutcome(
        verdict="FAIL",
        reason="actor is not the responsible principal",
    )


def _guard_actor_can_cancel(
    evaluation: GuardEvaluationInput,
) -> GuardOutcome:
    if evaluation.actor == evaluation.responsible:
        return GuardOutcome(verdict="PASS", reason="actor may cancel the task")
    return GuardOutcome(
        verdict="FAIL",
        reason="actor is not the responsible principal",
    )


def _guard_required_artifacts_present(
    evaluation: GuardEvaluationInput,
) -> GuardOutcome:
    artifacts = evaluation.payload.get("artifacts")
    if isinstance(artifacts, list) and artifacts:
        return GuardOutcome(verdict="PASS", reason="artifacts present in payload")
    return GuardOutcome(verdict="FAIL", reason="no artifacts in payload")


def _guard_event_matches_task(
    evaluation: GuardEvaluationInput,
) -> GuardOutcome:
    if "task_id" not in evaluation.payload:
        return GuardOutcome(verdict="PASS", reason="no explicit task_id in payload")
    return GuardOutcome(
        verdict="FAIL",
        reason="explicit task_id not supported in P1",
    )


# 守卫注册表:guard_refs 只能引用这里已注册的纯谓词(§4.3)。
GUARD_REGISTRY: dict[str, GuardFunction] = {
    "actor_can_start_task": _guard_actor_can_start_task,
    "actor_is_responsible_principal": _guard_actor_is_responsible_principal,
    "actor_can_cancel": _guard_actor_can_cancel,
    "required_artifacts_present": _guard_required_artifacts_present,
    "event_matches_task": _guard_event_matches_task,
}


# ------------------------------------------------------- 内置 machine(spec §4.1 重命名)

TEAM_TODO_EXECUTION_MACHINE = StateMachineDefinition(
    schema_version="context-state-machine/1",
    profile_ref="team_todo_execution",
    profile_version=1,
    compatibility_policy="new_contexts_only",
    initial_state="created",
    states=(
        StateDefinition(
            name="created", governance_kind="waiting", allowed_methods=("Single",)
        ),
        StateDefinition(
            name="in_progress", governance_kind="active", allowed_methods=("Single",)
        ),
        StateDefinition(
            name="reviewing", governance_kind="review", allowed_methods=("Single",)
        ),
        StateDefinition(
            name="blocked", governance_kind="blocked", allowed_methods=("Single",)
        ),
        # Todo acceptance is owned by A, so B's ``done`` means "this executor
        # round is finished"; a Human interaction can resume the same Context.
        StateDefinition(
            name="done", governance_kind="review", allowed_methods=("Single",)
        ),
        StateDefinition(
            name="cancelled",
            governance_kind="terminal_cancelled",
            allowed_methods=("Single",),
        ),
    ),
    transitions=(
        TransitionRule(
            rule_id="start_task",
            from_states=("created",),
            event_name="task.started",
            guard_refs=("actor_can_start_task",),
            to_state="in_progress",
        ),
        TransitionRule(
            rule_id="update_progress",
            from_states=("in_progress",),
            event_name="task.progress.updated",
            guard_refs=("event_matches_task",),
            to_state="in_progress",
        ),
        TransitionRule(
            rule_id="complete_task",
            from_states=("in_progress",),
            event_name="task.completed",
            guard_refs=("event_matches_task",),
            to_state="done",
        ),
        TransitionRule(
            rule_id="resume_task",
            from_states=("done",),
            event_name="task.started",
            guard_refs=("actor_can_start_task",),
            to_state="in_progress",
        ),
        TransitionRule(
            rule_id="submit_for_review",
            from_states=("in_progress",),
            event_name="task.submitted",
            guard_refs=("required_artifacts_present",),
            to_state="reviewing",
        ),
        TransitionRule(
            rule_id="accept_task",
            from_states=("reviewing",),
            event_name="task.accepted",
            guard_refs=("actor_is_responsible_principal",),
            to_state="done",
        ),
        TransitionRule(
            rule_id="reject_task",
            from_states=("reviewing",),
            event_name="task.rejected",
            guard_refs=("actor_is_responsible_principal",),
            to_state="in_progress",
        ),
        TransitionRule(
            rule_id="cancel_task",
            from_states=("created", "in_progress", "reviewing", "blocked"),
            event_name="task.cancelled",
            guard_refs=("actor_can_cancel",),
            to_state="cancelled",
        ),
    ),
)

# P1 唯一内置 machine;不发明 forum 状态。
MACHINE_REGISTRY: dict[str, StateMachineDefinition] = {
    "team_todo_execution": TEAM_TODO_EXECUTION_MACHINE,
}

# 内置 machine 注册即过 Conformance:若字面量漂移,导入期立即失败。
validate_definition(TEAM_TODO_EXECUTION_MACHINE)
