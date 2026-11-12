"""Task 9:Single 方法剖面(SingleSelection + Single 四操作)单测。

覆盖(spec §4.1 + RFC-301 §9 五条一致性断言逐条):
- 值对象:round-trip / 状态 wire 校验 / 未知字段拒绝 / refs 校验 /
  G4 注册机器词汇对齐(防漂移);
- store 切片:幂等重放、同 (binding, scope) 至多一个 ACTIVE、
  ``replace_selection`` 锁内原子换手旧→REPLACED / 新→ACTIVE、
  换手冲突 409 语义、``update_selection_state`` 缺键 NotFound;
- 服务四操作:select / replace / release / delegate(=嵌套 binding +
  delegate_to 边,复用 Task 8 lifecycle 编排);
- RFC-301 §9:五条一致性断言各一测(唯一 ACTIVE、被选身份是 agent
  参与者、Query 结果不自动成为 selection、replace 单一可排序事件、
  嵌套委托不动上游职责);
- RF#2:binding 非 ACTIVE 四个副作用操作一律拒绝;
- T6 default-bucket pin:select/replace/release/delegate 不在任何治理
  frozenset,默认治理桶路由——owner 放行、陌生拒绝(关闭 T6-minor-1);

collet 仓储用手写 fake(受控返回 ``ContextStatusRecord``),store 用真
``InMemoryMethodStore``,lifecycle 用真 ``MethodLifecycleService``。
"""

from dataclasses import replace

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    GROUP_OWNER_OPS,
    GROUP_SELF_HELP_OPS,
    StructuralMethodOpsAuthorizer,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.method_state_machines import (
    METHOD_MACHINE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ContextSpec,
    CurrentSnapshot,
    ProgressSnapshot,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    TaskStatus,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
)
from agentclaw.community.core.task.context_collab.l5_methods.single_profile import (
    SelectionState,
    SingleSelection,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    BindMethodCommand,
    MethodStateChangeCommand,
    SingleDelegateCommand,
    SingleReplaceCommand,
    SingleSelectCommand,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.services.method_lifecycle_service import (
    MethodLifecycleService,
    current_axes,
)
from agentclaw.community.core.task.context_collab.services.single_group_method_service import (
    SingleGroupMethodService,
)

COLLET_ID = "collet_single_001"
PRINCIPAL_ID = "u_1001"
EXECUTOR_ID = "bot_coding_001"
NOW = "2026-11-12T08:00:00+00:00"

OBJECTIVE = "为 RFC-301 Single 剖面的一致性断言落测试"

AXES = current_axes()


def make_actor(principal_id: str = PRINCIPAL_ID) -> ActorContext:
    return ActorContext(actor=PrincipalRef("human", principal_id), source="api")


def _context_spec(*, method_kind: str = "Single") -> ContextSpec:
    return ContextSpec(
        space=SpaceRef(space_id="space_workspace_001", environment="prod"),
        principal_ref=PrincipalRef("human", PRINCIPAL_ID),
        method_kind=method_kind,
        executor_ref=AgentRef("bot", EXECUTOR_ID),
        objective=OBJECTIVE,
        resource_refs=(),
        product_kind=None,
        workflow_kind=None,
    )


def _status_record(**overrides) -> ContextStatusRecord:
    """构造受控 ``ContextStatusRecord``(与 lifecycle 单测同款)。"""
    fields = {
        "collab_context_id": COLLET_ID,
        "status": TaskStatus.IN_PROGRESS.value,
        "method_kind": "Single",
    }
    fields.update(overrides)
    spec = _context_spec(method_kind=fields["method_kind"])
    principal_ref = PrincipalRef("human", PRINCIPAL_ID)
    executor_ref = AgentRef("bot", EXECUTOR_ID)
    collet = Collet(
        collab_context_id=fields["collab_context_id"],
        actor_ref=None,
        context_spec=spec,
        current_snapshot=CurrentSnapshot(
            objective=OBJECTIVE,
            status=fields["status"],
            principal_ref=principal_ref,
            executor_ref=executor_ref,
            method_kind=fields["method_kind"],
            progress=ProgressSnapshot(),
        ),
        current_event_id=None,
        context_version=1,
    )
    return ContextStatusRecord(collet=collet, events=[])


class _FakeCollabRepository:
    """最小 collet 仓储 fake:按 context 返回受控 ``ContextStatusRecord``。"""

    def __init__(self, records: dict[str, ContextStatusRecord]) -> None:
        self._records = dict(records)

    def get_current_status(self, collab_context_id: str):
        return self._records.get(collab_context_id)

    def create_context(self, request):
        raise NotImplementedError("fake only implements get_current_status")

    def append_event(self, collab_context_id: str, request):
        raise NotImplementedError("fake only implements get_current_status")


def make_single_service():
    """真 store + 真 authorizer+真 lifecycle + 受控 fake 仓储的服务四件套。"""
    store = InMemoryMethodStore()
    repo = _FakeCollabRepository({COLLET_ID: _status_record()})
    lifecycle = MethodLifecycleService(
        store=store,
        authorizer=StructuralMethodOpsAuthorizer(),
        collab_repo=repo,
    )
    single = SingleGroupMethodService(
        store=store,
        authorizer=StructuralMethodOpsAuthorizer(),
        collab_repo=repo,
        lifecycle=lifecycle,
    )
    return store, repo, lifecycle, single


def active_single_binding(lifecycle: MethodLifecycleService,
                          binding_id: str = "mb_single_alpha",
                          collab_context_id: str = COLLET_ID) -> str:
    """bind + activate 一条嵌套 Single binding(single 四操作的前置)。"""
    lifecycle.bind(
        collab_context_id,
        BindMethodCommand(
            method_kind="Single",
            method_binding_id=binding_id,
            parent_scope_ref="task_scope_root",
            bind_reason="compose",
        ),
        make_actor(),
    )
    lifecycle.activate(
        collab_context_id, binding_id, MethodStateChangeCommand(), make_actor()
    )
    return binding_id


def select_agent(
    single: SingleGroupMethodService,
    binding_id: str = "mb_single_alpha",
    *,
    agent: str = "bot_alpha_001",
    scope_ref: str = "task_scope_001",
    actor: ActorContext | None = None,
    collab_context_id: str = COLLET_ID,
    **command_overrides,
):
    """经 ``select`` 落一次选择;command 字段可覆盖。"""
    fields = {
        "agent_participant_ref": agent,
        "scope_ref": scope_ref,
        "reason": "",
    }
    fields.update(command_overrides)
    return single.select(
        collab_context_id, binding_id, SingleSelectCommand(**fields),
        actor or make_actor(),
    )


def selection_dict(**overrides) -> dict:
    """SingleSelection 的 ``from_dict`` 输入基型;字段可覆盖。"""
    fields = {
        "selection_id": "sel_001",
        "method_binding_id": "mb_single_alpha",
        "scope_ref": "task_scope_001",
        "selected_agent_participant_ref": "bot_alpha_001",
        "selected_by_ref": PRINCIPAL_ID,
        "requirement_refs": [],
        "selection_evidence_refs": [],
        "state": "ACTIVE",
        "valid_from": NOW,
        "valid_until": None,
        "transitioned_from": "PROPOSED",
        "transition_reason": "single.select",
        "transitioned_by": PRINCIPAL_ID,
        "transitioned_at": NOW,
    }
    fields.update(overrides)
    return fields


# ---------------------------------------------------------------------------
# 值对象(spec §4.1 YAML + §6.3 审计四字段)
# ---------------------------------------------------------------------------


class TestSingleSelectionValue:
    def test_round_trip(self):
        data = selection_dict(
            requirement_refs=["req_py_001"],
            selection_evidence_refs=["ev_001"],
            valid_until="2026-12-01T00:00:00+00:00",
        )
        selection = SingleSelection.from_dict(data)
        assert selection.to_dict() == data
        assert SingleSelection.from_dict(selection.to_dict()) == selection

    def test_state_wire_validation(self):
        assert SelectionState.from_wire("PROPOSED") is SelectionState.PROPOSED
        assert SelectionState.from_wire("REPLACED") is SelectionState.REPLACED
        with pytest.raises(ContextCollabValidationError, match="wire"):
            SelectionState.from_wire("PAUSED")

    def test_state_vocabulary_matches_registered_machine(self):
        """防漂移:``SingleSelection`` 状态词汇与 G4 注册机器逐字一致。"""
        machine = METHOD_MACHINE_REGISTRY["single_selection"]
        assert {state.name for state in machine.states} == {
            state.value for state in SelectionState
        }

    def test_unknown_field_rejected(self):
        with pytest.raises(ContextCollabValidationError, match="unknown field"):
            SingleSelection.from_dict({**selection_dict(), "extra_field": 1})

    def test_required_refs_non_empty(self):
        for field in (
            "selection_id",
            "method_binding_id",
            "scope_ref",
            "selected_agent_participant_ref",
            "selected_by_ref",
            "valid_from",
        ):
            with pytest.raises(ContextCollabValidationError):
                SingleSelection.from_dict(selection_dict(**{field: ""}))

    def test_refs_missing_to_empty_and_empty_entry_rejected(self):
        data = selection_dict()
        data.pop("requirement_refs")
        data.pop("selection_evidence_refs")
        selection = SingleSelection.from_dict(data)
        assert selection.requirement_refs == ()
        assert selection.selection_evidence_refs == ()
        with pytest.raises(ContextCollabValidationError, match="requirement_refs"):
            SingleSelection.from_dict(selection_dict(requirement_refs=[""]))

    def test_valid_until_none_or_nonempty(self):
        assert SingleSelection.from_dict(selection_dict(valid_until=None)).valid_until is None
        with pytest.raises(ContextCollabValidationError, match="valid_until"):
            SingleSelection.from_dict(selection_dict(valid_until=""))

    def test_audit_fields_required_but_may_be_empty(self):
        selection = SingleSelection.from_dict(selection_dict(transition_reason=""))
        assert selection.transition_reason == ""
        data = selection_dict()
        data.pop("transition_reason")
        with pytest.raises(ContextCollabValidationError, match="transition_reason"):
            SingleSelection.from_dict(data)


# ---------------------------------------------------------------------------
# store 切片(唯一 ACTIVE 断言 + 锁内原子换手;spec §6.1/§4.1)
# ---------------------------------------------------------------------------


class TestSelectionStoreSlice:
    def test_insert_idempotent_same_shape_conflict_different(self):
        """§8.3 幂等键语义:同键同 to_dict 返回既有实例,异形态拒绝。"""
        store = InMemoryMethodStore()
        selection = SingleSelection.from_dict(selection_dict())
        assert store.insert_selection(AXES, selection, COLLET_ID) is selection
        again = SingleSelection.from_dict(selection_dict())
        assert store.insert_selection(AXES, again, COLLET_ID) is selection
        drifted = SingleSelection.from_dict(
            selection_dict(transition_reason="rerun")
        )
        with pytest.raises(
            ContextCollabValidationError, match="differs from the stored shape"
        ):
            store.insert_selection(AXES, drifted, COLLET_ID)

    def test_insert_enforces_single_active_per_binding_scope(self):
        """唯一性断言 1 的存储层强制:同 (binding, scope) 至多一个 ACTIVE;
        不同 scope 互不占用,非 ACTIVE 对象(REPLACED/RELEASED)可正常插入。"""
        store = InMemoryMethodStore()
        first = store.insert_selection(
            AXES, SingleSelection.from_dict(selection_dict()), COLLET_ID
        )
        duplicate = SingleSelection.from_dict(
            selection_dict(selection_id="sel_002", selected_agent_participant_ref="bot_beta_001")
        )
        with pytest.raises(ContextCollabValidationError, match="ACTIVE"):
            store.insert_selection(AXES, duplicate, COLLET_ID)
        other_scope = SingleSelection.from_dict(
            selection_dict(selection_id="sel_003", scope_ref="task_scope_002")
        )
        assert store.insert_selection(AXES, other_scope, COLLET_ID) is other_scope
        released = SingleSelection.from_dict(
            selection_dict(selection_id="sel_004", state="RELEASED")
        )
        assert store.insert_selection(AXES, released, COLLET_ID) is released
        # 回查与现役查询。
        assert store.get_selection(AXES, "sel_001", "mb_single_alpha", COLLET_ID) is first
        assert store.get_selection(AXES, "sel_missing", "mb_single_alpha", COLLET_ID) is None
        assert (
            store.active_selection(AXES, "mb_single_alpha", "task_scope_001", COLLET_ID)
            is first
        )
        assert (
            store.active_selection(AXES, "mb_single_alpha", "task_scope_002", COLLET_ID)
            is other_scope
        )
        assert (
            store.active_selection(AXES, "mb_single_alpha", "task_scope_none", COLLET_ID)
            is None
        )
        # list 保持插入序,不过滤状态。
        listed = store.list_selections(AXES, "mb_single_alpha", COLLET_ID)
        assert [item.selection_id for item in listed] == [
            "sel_001",
            "sel_003",
            "sel_004",
        ]

    def test_replace_selection_atomic_pair_and_conflicts(self):
        """原子换手:旧 REPLACED + 新 ACTIVE 一次落定;现役换手后再以旧
        selection 宣称换手抛 409 语义冲突;键域不一致/缺键各自拒绝。"""
        store = InMemoryMethodStore()
        old = store.insert_selection(
            AXES, SingleSelection.from_dict(selection_dict()), COLLET_ID
        )
        old_replaced = replace(
            old,
            state=SelectionState.REPLACED,
            transitioned_from="ACTIVE",
            transition_reason="single.replace",
            transitioned_by=PRINCIPAL_ID,
            transitioned_at="2026-11-12T09:00:00+00:00",
        )
        new_active = SingleSelection.from_dict(
            selection_dict(
                selection_id="sel_002",
                selected_agent_participant_ref="bot_beta_001",
                transitioned_at="2026-11-12T09:00:01+00:00",
            )
        )
        store.replace_selection(AXES, old_replaced, new_active, COLLET_ID)
        assert (
            store.get_selection(AXES, "sel_001", "mb_single_alpha", COLLET_ID).state
            is SelectionState.REPLACED
        )
        assert (
            store.active_selection(AXES, "mb_single_alpha", "task_scope_001", COLLET_ID)
            is new_active
        )
        # 现役已换手到 sel_002:再以 sel_001 宣称换手 → 版本冲突。
        stale_claim = replace(old_replaced, transition_reason="stale")
        with pytest.raises(ContextCollabVersionConflict):
            store.replace_selection(
                AXES,
                stale_claim,
                SingleSelection.from_dict(selection_dict(selection_id="sel_003")),
                COLLET_ID,
            )
        # 新旧必须同 (method_binding_id, scope_ref)。
        with pytest.raises(ContextCollabValidationError, match="scope_ref"):
            store.replace_selection(
                AXES,
                old_replaced,
                SingleSelection.from_dict(
                    selection_dict(selection_id="sel_009", scope_ref="task_scope_other")
                ),
                COLLET_ID,
            )
        # 新旧 selection_id 相异(同 id 走 update_selection_state)。
        with pytest.raises(ContextCollabValidationError, match="selection_id"):
            store.replace_selection(
                AXES,
                old_replaced,
                SingleSelection.from_dict(selection_dict(selection_id="sel_001")),
                COLLET_ID,
            )
        # 旧对象缺键 → NotFound。
        ghost = SingleSelection.from_dict(selection_dict(selection_id="sel_ghost"))
        with pytest.raises(ContextCollabNotFoundError):
            store.replace_selection(
                AXES,
                ghost,
                SingleSelection.from_dict(selection_dict(selection_id="sel_010")),
                COLLET_ID,
            )

    def test_update_selection_state_overwrites_and_missing_rejects(self):
        store = InMemoryMethodStore()
        active = store.insert_selection(
            AXES, SingleSelection.from_dict(selection_dict()), COLLET_ID
        )
        released = replace(
            active,
            state=SelectionState.RELEASED,
            transitioned_from="ACTIVE",
            transition_reason="single.release",
            transitioned_by=PRINCIPAL_ID,
            transitioned_at="2026-11-12T10:00:00+00:00",
        )
        store.update_selection_state(AXES, released, COLLET_ID)
        assert (
            store.get_selection(AXES, "sel_001", "mb_single_alpha", COLLET_ID).state
            is SelectionState.RELEASED
        )
        ghost = SingleSelection.from_dict(selection_dict(selection_id="sel_missing"))
        with pytest.raises(ContextCollabNotFoundError):
            store.update_selection_state(AXES, ghost, COLLET_ID)

    def test_selection_keys_are_context_scoped(self):
        """回归(T9 fix):selection 键位锚定 collab_context_id——两个协作
        上下文可合法持有同 id 的 binding,其名下同 binding id + 同
        selection id 互不冲突(各自独立成行),同 (binding, scope) 的
        ACTIVE 唯一性按上下文独立裁决,读面不跨上下文泄漏。"""
        store = InMemoryMethodStore()
        other_ctx = "collet_single_002"
        first = store.insert_selection(
            AXES, SingleSelection.from_dict(selection_dict()), COLLET_ID
        )
        second = store.insert_selection(
            AXES,
            SingleSelection.from_dict(
                selection_dict(selected_agent_participant_ref="bot_other_001")
            ),
            other_ctx,
        )
        # 同 selection id 两个上下文各自独立成行。
        assert first is not second
        assert store.get_selection(AXES, "sel_001", "mb_single_alpha", COLLET_ID) is first
        assert store.get_selection(AXES, "sel_001", "mb_single_alpha", other_ctx) is second
        # ACTIVE 唯一性按上下文独立:两个上下文各持一个现役,互不占用。
        assert (
            store.active_selection(AXES, "mb_single_alpha", "task_scope_001", COLLET_ID)
            is first
        )
        assert (
            store.active_selection(AXES, "mb_single_alpha", "task_scope_001", other_ctx)
            is second
        )
        # list 只见各自上下文的行。
        assert [
            item.selection_id
            for item in store.list_selections(AXES, "mb_single_alpha", COLLET_ID)
        ] == ["sel_001"]
        assert [
            item.selection_id
            for item in store.list_selections(AXES, "mb_single_alpha", other_ctx)
        ] == ["sel_001"]


# ---------------------------------------------------------------------------
# 服务四操作(RFC-301 §9 五条一致性断言逐条 + RF#2 + T6 pin)
# ---------------------------------------------------------------------------


class TestSingleSelect:
    def test_select_creates_active_selection(self):
        store, _, lifecycle, single = make_single_service()
        binding_id = active_single_binding(lifecycle)
        result = select_agent(
            single,
            binding_id,
            selection_id="sel_custom_1",
            reason="首选执行者",
            valid_until="2026-12-01T00:00:00+00:00",
            requirement_refs=["req_py_001"],
            selection_evidence_refs=["ev_001"],
        )
        assert result.accepted is True
        assert result.object_kind == "single_selection"
        assert result.object_id == "sel_custom_1"
        assert result.state == "ACTIVE"
        stored = store.get_selection(AXES, "sel_custom_1", binding_id, COLLET_ID)
        assert stored.state is SelectionState.ACTIVE
        assert stored.selected_agent_participant_ref == "bot_alpha_001"
        assert stored.selected_by_ref == PRINCIPAL_ID
        assert stored.requirement_refs == ("req_py_001",)
        assert stored.selection_evidence_refs == ("ev_001",)
        assert stored.valid_until == "2026-12-01T00:00:00+00:00"
        assert stored.transitioned_from == "PROPOSED"
        assert stored.transition_reason == "首选执行者"
        assert stored.transitioned_by == PRINCIPAL_ID
        assert stored.transitioned_at
        # 不传幂等键:服务端生成 sel_ 前缀。
        generated = select_agent(single, binding_id, scope_ref="task_scope_gen")
        assert generated.object_id.startswith("sel_")
        assert generated.state == "ACTIVE"

    def test_select_missing_binding_not_found(self):
        _, _, _, single = make_single_service()
        with pytest.raises(ContextCollabNotFoundError):
            single.select(
                COLLET_ID,
                "mb_ghost_001",
                SingleSelectCommand(
                    agent_participant_ref="bot_x", scope_ref="task_scope_001"
                ),
                make_actor(),
            )


class TestSelectionScopingAndReplay:
    """T9 fix 回归:selection 状态按 collab_context 域隔离 + select §8.3 重放。"""

    def test_same_binding_id_across_contexts_does_not_collide(self):
        """两个 Collet 可合法绑定同一 binding id:上下文 A 的 ACTIVE
        selection 不得阻塞上下文 B 对同 id/scope 的 select,读面不泄漏。"""
        store = InMemoryMethodStore()
        other_collet = "collet_single_002"
        repo = _FakeCollabRepository(
            {
                COLLET_ID: _status_record(),
                other_collet: _status_record(collab_context_id=other_collet),
            }
        )
        lifecycle = MethodLifecycleService(
            store=store,
            authorizer=StructuralMethodOpsAuthorizer(),
            collab_repo=repo,
        )
        single = SingleGroupMethodService(
            store=store,
            authorizer=StructuralMethodOpsAuthorizer(),
            collab_repo=repo,
            lifecycle=lifecycle,
        )
        shared_binding = "mb_shared_001"
        active_single_binding(lifecycle, shared_binding, COLLET_ID)
        active_single_binding(lifecycle, shared_binding, other_collet)
        # 同 binding id + 同 scope,两个上下文各自 select 成功。
        first = select_agent(single, shared_binding, collab_context_id=COLLET_ID)
        second = select_agent(single, shared_binding, collab_context_id=other_collet)
        assert first.accepted is True and second.accepted is True
        assert first.object_id != second.object_id
        assert (
            store.active_selection(
                AXES, shared_binding, "task_scope_001", COLLET_ID
            ).selection_id
            == first.object_id
        )
        assert (
            store.active_selection(
                AXES, shared_binding, "task_scope_001", other_collet
            ).selection_id
            == second.object_id
        )

    def test_select_replay_same_shape_returns_existing(self):
        """§8.3 幂等重放(与 bind 同款裁决):同 selection_id 同请求形态的
        select 重试返回既有已受理对象,不新增行、不重写审计;服务端时间戳
        (valid_from / transitioned_at)逐次不同,不计入形态比对。"""
        store, _, lifecycle, single = make_single_service()
        binding_id = active_single_binding(lifecycle)
        command_fields = dict(
            selection_id="sel_replay_1",
            agent="bot_alpha_001",
            scope_ref="task_scope_001",
            reason="首选执行者",
            valid_until="2026-12-01T00:00:00+00:00",
            requirement_refs=["req_py_001"],
        )
        first = select_agent(single, binding_id, **command_fields)
        assert first.accepted is True
        stored_first = store.get_selection(
            AXES, "sel_replay_1", binding_id, COLLET_ID
        )
        replay = select_agent(single, binding_id, **command_fields)
        assert replay.accepted is True
        assert replay.object_id == first.object_id
        assert replay.state == stored_first.state.value
        # 既有对象原样返回:不新增行、审计不被重放覆写。
        assert (
            store.get_selection(AXES, "sel_replay_1", binding_id, COLLET_ID)
            is stored_first
        )
        assert len(store.list_selections(AXES, binding_id, COLLET_ID)) == 1

    def test_select_replay_different_shape_conflicts(self):
        """同 selection_id 但请求形态漂移(换 agent / 换 scope)的重放按
        409 语义拒绝,既有对象不被覆写。"""
        store, _, lifecycle, single = make_single_service()
        binding_id = active_single_binding(lifecycle)
        select_agent(
            single,
            binding_id,
            selection_id="sel_replay_2",
            agent="bot_alpha_001",
        )
        stored_first = store.get_selection(
            AXES, "sel_replay_2", binding_id, COLLET_ID
        )
        with pytest.raises(
            ContextCollabValidationError, match="differs from the stored shape"
        ):
            select_agent(
                single,
                binding_id,
                selection_id="sel_replay_2",
                agent="bot_beta_001",
            )
        assert (
            store.get_selection(AXES, "sel_replay_2", binding_id, COLLET_ID)
            is stored_first
        )
        assert len(store.list_selections(AXES, binding_id, COLLET_ID)) == 1


class TestRfc301Assertions:
    def test_exactly_one_active_per_scope(self):
        """断言 1:同一 binding + scope 至多一个 ACTIVE selection。"""
        store, _, lifecycle, single = make_single_service()
        binding_id = active_single_binding(lifecycle)
        first = select_agent(single, binding_id, agent="bot_alpha_001")
        assert first.state == "ACTIVE"
        with pytest.raises(ContextCollabValidationError, match="ACTIVE"):
            select_agent(single, binding_id, agent="bot_beta_001")
        # 不同 scope 互不占用。
        second = select_agent(
            single, binding_id, agent="bot_gamma_001", scope_ref="task_scope_002"
        )
        assert second.state == "ACTIVE"

    def test_selected_identity_is_agent_participant(self):
        """断言 2:被选身份就是 agent 参与者引用,与选择者分离;
        空引用在值对象构造层即拒绝。"""
        store, _, lifecycle, single = make_single_service()
        binding_id = active_single_binding(lifecycle)
        select_agent(single, binding_id, agent="bot_alpha_001")
        stored = store.active_selection(AXES, binding_id, "task_scope_001", COLLET_ID)
        assert stored.selected_agent_participant_ref == "bot_alpha_001"
        assert stored.selected_by_ref == PRINCIPAL_ID
        with pytest.raises(
            ContextCollabValidationError, match="selected_agent_participant_ref"
        ):
            SingleSelection.from_dict(
                selection_dict(selected_agent_participant_ref="")
            )

    def test_query_result_never_becomes_selection(self):
        """断言 3:Query 结果不自动成为 selection——Select 必须显式携带
        agent 引用;空引用拒绝且不落任何新行。"""
        store, _, lifecycle, single = make_single_service()
        binding_id = active_single_binding(lifecycle)
        with pytest.raises(ContextCollabValidationError, match="agent_participant_ref"):
            select_agent(single, binding_id, agent="")
        assert store.active_selection(AXES, binding_id, "task_scope_001", COLLET_ID) is None
        assert store.list_selections(AXES, binding_id, COLLET_ID) == []

    def test_replace_is_single_ordered_event(self):
        """断言 4:Replace 是单一、可排序事件——存储锁内一次落定
        旧 ACTIVE→REPLACED / 新选择→ACTIVE,各自审计恰一次。"""
        store, _, lifecycle, single = make_single_service()
        binding_id = active_single_binding(lifecycle)
        selected = select_agent(single, binding_id, agent="bot_alpha_001")
        result = single.replace(
            COLLET_ID,
            binding_id,
            SingleReplaceCommand(
                agent_participant_ref="bot_beta_001",
                scope_ref="task_scope_001",
                reason="换手",
            ),
            make_actor(),
        )
        assert result.accepted is True
        assert result.state == "ACTIVE"
        assert result.object_id != selected.object_id
        old = store.get_selection(AXES, selected.object_id, binding_id, COLLET_ID)
        assert old.state is SelectionState.REPLACED
        assert old.transitioned_from == "ACTIVE"
        assert old.transition_reason == "换手"
        assert old.transitioned_by == PRINCIPAL_ID
        assert old.transitioned_at
        new = store.active_selection(AXES, binding_id, "task_scope_001", COLLET_ID)
        assert new.selection_id == result.object_id
        assert new.state is SelectionState.ACTIVE
        assert new.selected_agent_participant_ref == "bot_beta_001"
        assert new.transitioned_from == "PROPOSED"
        assert new.transition_reason == "换手"
        assert new.transitioned_by == PRINCIPAL_ID
        assert new.transitioned_at
        # 同 scope 换手后仍只有一个 ACTIVE。
        assert new.scope_ref == old.scope_ref == "task_scope_001"

    def test_release_moves_active_to_released(self):
        """Release:ACTIVE→RELEASED,释放后同 scope 可再选。"""
        store, _, lifecycle, single = make_single_service()
        binding_id = active_single_binding(lifecycle)
        selected = select_agent(single, binding_id, agent="bot_alpha_001")
        result = single.release(
            COLLET_ID,
            binding_id,
            MethodStateChangeCommand(reason="任务取消"),
            make_actor(),
        )
        assert result.accepted is True
        assert result.object_kind == "single_selection"
        assert result.object_id == selected.object_id
        assert result.state == "RELEASED"
        released = store.get_selection(AXES, selected.object_id, binding_id, COLLET_ID)
        assert released.state is SelectionState.RELEASED
        assert released.transitioned_from == "ACTIVE"
        assert released.transition_reason == "任务取消"
        assert released.transitioned_by == PRINCIPAL_ID
        assert store.active_selection(AXES, binding_id, "task_scope_001", COLLET_ID) is None
        again = select_agent(single, binding_id, agent="bot_gamma_001")
        assert again.state == "ACTIVE"

    def test_nested_delegation_keeps_upstream_responsibility(self):
        """断言 5:Delegate 只新增嵌套 binding + DELEGATE_TO 边,
        上游 selection 仍 ACTIVE 且审计不被触碰。"""
        store, _, lifecycle, single = make_single_service()
        binding_id = active_single_binding(lifecycle)
        selected = select_agent(single, binding_id, agent="bot_alpha_001")
        edge = single.delegate(
            COLLET_ID,
            binding_id,
            SingleDelegateCommand(
                method_kind="Swarm",
                parent_scope_ref="task_scope_nested",
                reason="委托子任务",
            ),
            make_actor(),
        )
        assert edge.edge["relation_kind"] == "delegate_to"
        assert edge.edge["parent_binding"] == binding_id
        assert edge.edge["state"] == "ACTIVE"
        child_id = edge.edge["child_binding"]
        assert child_id.startswith("mb_")
        nested = store.get_binding(AXES, child_id, COLLET_ID)
        assert nested.method_kind == "Swarm"
        assert nested.state is BindingState.BOUND
        assert nested.bind_reason == "delegate"
        assert nested.parent_scope_ref == "task_scope_nested"
        # 上游职责不动:selection 仍 ACTIVE、审计未覆写。
        upstream = store.active_selection(AXES, binding_id, "task_scope_001", COLLET_ID)
        assert upstream.selection_id == selected.object_id
        assert upstream.state is SelectionState.ACTIVE
        assert upstream.transitioned_from == "PROPOSED"
        assert upstream.transition_reason == "single.select"

    def test_delegate_without_scope_constraint_is_legal(self):
        """Task-7 遗留裁决 pin(coordinator,不再重议):
        ``SingleDelegateCommand.parent_scope_ref=None`` 合法——语义是
        "不带作用域约束的委托",与 bind 命令的 ``parent_scope_ref:
        str | None`` 一致;嵌套 binding 落 ``parent_scope_ref=None``。"""
        store, _, lifecycle, single = make_single_service()
        binding_id = active_single_binding(lifecycle)
        edge = single.delegate(
            COLLET_ID,
            binding_id,
            SingleDelegateCommand(method_kind="Swarm", parent_scope_ref=None),
            make_actor(),
        )
        nested = store.get_binding(AXES, edge.edge["child_binding"], COLLET_ID)
        assert nested.bind_reason == "delegate"
        assert nested.parent_scope_ref is None

    def test_delegate_failure_propagates_bind_validation(self):
        """非法 method_kind wire 值经 Task 8 bind 原样上抛,无半态落库。"""
        store, _, lifecycle, single = make_single_service()
        binding_id = active_single_binding(lifecycle)
        before = len(store.list_bindings(AXES, COLLET_ID))
        with pytest.raises(ContextCollabValidationError, match="unknown method_kind"):
            single.delegate(
                COLLET_ID,
                binding_id,
                SingleDelegateCommand(
                    method_kind="Quantum", parent_scope_ref="task_scope_bad"
                ),
                make_actor(),
            )
        assert len(store.list_bindings(AXES, COLLET_ID)) == before

    def test_replace_and_release_without_active_selection_not_found(self):
        _, _, lifecycle, single = make_single_service()
        binding_id = active_single_binding(lifecycle)
        with pytest.raises(ContextCollabNotFoundError):
            single.replace(
                COLLET_ID,
                binding_id,
                SingleReplaceCommand(
                    agent_participant_ref="bot_x", scope_ref="task_scope_001"
                ),
                make_actor(),
            )
        with pytest.raises(ContextCollabNotFoundError):
            single.release(
                COLLET_ID, binding_id, MethodStateChangeCommand(), make_actor()
            )


class TestSingleOpsGuards:
    def test_single_ops_reject_non_active_binding(self):
        """RF#2:binding 非 ACTIVE(BOUND/SUSPENDED)时四个副作用操作一律拒绝。"""
        _, _, lifecycle, single = make_single_service()
        lifecycle.bind(
            COLLET_ID,
            BindMethodCommand(
                method_kind="Single",
                method_binding_id="mb_bound_ops",
                parent_scope_ref="task_scope_root",
            ),
            make_actor(),
        )
        with pytest.raises(ContextCollabValidationError, match="ACTIVE"):
            select_agent(single, "mb_bound_ops")
        binding_id = active_single_binding(lifecycle, "mb_susp_ops")
        lifecycle.suspend(
            COLLET_ID, binding_id, MethodStateChangeCommand(), make_actor()
        )
        with pytest.raises(ContextCollabValidationError, match="ACTIVE"):
            select_agent(single, binding_id)
        with pytest.raises(ContextCollabValidationError, match="ACTIVE"):
            single.replace(
                COLLET_ID,
                binding_id,
                SingleReplaceCommand(
                    agent_participant_ref="bot_x", scope_ref="task_scope_001"
                ),
                make_actor(),
            )
        with pytest.raises(ContextCollabValidationError, match="ACTIVE"):
            single.release(
                COLLET_ID, binding_id, MethodStateChangeCommand(), make_actor()
            )
        with pytest.raises(ContextCollabValidationError, match="ACTIVE"):
            single.delegate(
                COLLET_ID,
                binding_id,
                SingleDelegateCommand(
                    method_kind="Swarm", parent_scope_ref="task_scope_del"
                ),
                make_actor(),
            )

    def test_unlisted_single_ops_route_to_governance_bucket(self):
        """T6 default-bucket pin(T6-minor-1):select/replace/release/delegate
        不在任何治理 frozenset,经 check_group_op 默认治理桶路由——
        陌生人四面全拒,owner 四面全通过。"""
        single_ops = ("select", "replace", "release", "delegate")
        for op in single_ops:
            assert op not in GROUP_OWNER_OPS
            assert op not in GROUP_SELF_HELP_OPS
        store, _, lifecycle, single = make_single_service()
        binding_id = active_single_binding(lifecycle)
        selected = select_agent(single, binding_id)
        stranger = make_actor("u_9999")
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            select_agent(
                single, binding_id, scope_ref="task_scope_stranger", actor=stranger
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            single.replace(
                COLLET_ID,
                binding_id,
                SingleReplaceCommand(
                    agent_participant_ref="bot_x", scope_ref="task_scope_001"
                ),
                stranger,
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            single.release(
                COLLET_ID, binding_id, MethodStateChangeCommand(), stranger
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            single.delegate(
                COLLET_ID,
                binding_id,
                SingleDelegateCommand(
                    method_kind="Swarm", parent_scope_ref="task_scope_stranger"
                ),
                stranger,
            )
        # owner 全通过。
        replaced = single.replace(
            COLLET_ID,
            binding_id,
            SingleReplaceCommand(
                agent_participant_ref="bot_beta_001",
                scope_ref="task_scope_001",
                reason="owner 换手",
            ),
            make_actor(),
        )
        assert replaced.state == "ACTIVE"
        assert replaced.object_id != selected.object_id
        released = single.release(
            COLLET_ID, binding_id, MethodStateChangeCommand(), make_actor()
        )
        assert released.state == "RELEASED"
        delegated = single.delegate(
            COLLET_ID,
            binding_id,
            SingleDelegateCommand(
                method_kind="Group", parent_scope_ref="task_scope_owner_del"
            ),
            make_actor(),
        )
        assert delegated.edge["relation_kind"] == "delegate_to"
