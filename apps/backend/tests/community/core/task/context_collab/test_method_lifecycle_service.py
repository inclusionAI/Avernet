"""L5 Method 生命周期服务单测(Task 8:生命周期 + 顶层投影 + 读路径自愈)。

覆盖:
- 状态机迁移全矩阵 happy path(BOUND→ACTIVE→SUSPENDED→ACTIVE→TERMINATED)
  含 version 自增与审计四字段;
- 非法迁移拒绝(terminate 后 activate、重复 activate 幂等拒绝,§8.3);
- ``bind`` 幂等键语义(同 ID 同形态返回既有 / 异形态冲突,§8.3);
- ``compose`` 拒绝环与深度超限(§3.3/§12 第 3 条)、非 ACTIVE 父拒绝;
- ``detach`` 置 DETACHED 且仅 ``active_only=False`` 可见(审计保留);
- 授权接缝走真 ``StructuralMethodOpsAuthorizer``(陌生 actor 拒绝);
- 顶层 binding ``terminate`` 门槛(仅 Collet 快照任务终态放行,§3.2);
- 读路径自愈(§6.4:``ensure_top_level_binding`` 静默重建 ACTIVE 顶层);
- 顶层投影 ``project_created_context``(spec §3.2 初始 ACTIVE + §9.2 单向派生);
- ``update_config`` 乐观版本(不匹配 409 语义)与 config 形态校验;
- ``inspect`` 投影(state summary + capabilities + conformance evidence)。

collet 仓储用手写 fake(受控返回 ``ContextStatusRecord``),store 用真
``InMemoryMethodStore``;不触碰数据库。
"""

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    StructuralMethodOpsAuthorizer,
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
    ColletState,
)
from agentclaw.community.core.task.context_collab.l6_products.context_spec import (
    ContextSpec,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import Task
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    TaskStatus,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    BindMethodCommand,
    ComposeMethodCommand,
    MethodStateChangeCommand,
    UpdateBindingConfigCommand,
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
    ensure_top_level_binding,
    project_created_context,
    top_level_binding_id,
)
from tests.community.core.task.context_collab.product_catalog import PRODUCT_CATALOG

COLLET_ID = "collet_lifecycle_001"
PRINCIPAL_ID = "u_1001"
EXECUTOR_ID = "bot_coding_001"
NOW = "2026-11-12T08:00:00+00:00"

OBJECTIVE = "整理 ACCS-105 L5 权威面评审结论并落地生命周期服务"


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
    """构造受控 ``ContextStatusRecord``;``status`` 可覆盖以驱动门槛用例。"""
    fields = {
        "collab_context_id": COLLET_ID,
        "status": TaskStatus.IN_PROGRESS.value,
        "method_kind": "Single",
    }
    fields.update(overrides)
    spec = _context_spec(method_kind=fields["method_kind"])
    principal_ref = PrincipalRef("human", PRINCIPAL_ID)
    executor_ref = AgentRef("bot", EXECUTOR_ID)
    task = Task(
        objective=OBJECTIVE,
        status=fields["status"],
        principal_ref=principal_ref,
        executor_ref=executor_ref,
        task_id="task_" + fields["collab_context_id"],
        collet_ref=fields["collab_context_id"],
        version=1,
        parent_task_ref=None,
        completion_conditions=(),
        created_event_ref="evt_fixture",
        last_event_ref="evt_fixture",
    )
    collet = Collet(
        collet_id=fields["collab_context_id"],
        initiator_ref=principal_ref,
        state=ColletState.ACTIVE,
        scope=spec.space,
        current_event_id=None,
        version=1,
    )
    return ContextStatusRecord(
        collet=collet,
        context_spec=spec,
        tasks=(task,) if task is not None else (),
        task=task,
        events=[],
    )


class _FakeCollabRepository:
    """最小 collet 仓储 fake:按 context 返回受控 ``ContextStatusRecord``。"""

    def list_collets_by_product(self, product_kind: str):
        raise NotImplementedError("fake does not implement product listing")

    def count_message_events(self, collab_context_ids):
        raise NotImplementedError("fake does not implement message counting")

    def __init__(self, records: dict[str, ContextStatusRecord]) -> None:
        self._records = dict(records)

    def set_status(self, collab_context_id: str, status: str) -> None:
        self._records[collab_context_id] = _status_record(
            collab_context_id=collab_context_id, status=status
        )

    def get_current_status(self, collab_context_id: str):
        return self._records.get(collab_context_id)

    def create_context(self, request):
        raise NotImplementedError("fake only implements get_current_status")

    def append_event(self, collab_context_id: str, request):
        raise NotImplementedError("fake only implements get_current_status")


def make_service():
    """真 store + 真 authorizer + 受控 fake 仓储的服务三件套。"""
    store = InMemoryMethodStore()
    repo = _FakeCollabRepository({COLLET_ID: _status_record()})
    service = MethodLifecycleService(
        store=store,
        authorizer=StructuralMethodOpsAuthorizer(),
        collab_repo=repo,
        product_catalog=PRODUCT_CATALOG,
    )
    return store, repo, service


def bind_nested(service: MethodLifecycleService, binding_id: str, **overrides):
    """经 ``bind`` 落一条嵌套绑定(初始 BOUND);字段可覆盖。"""
    fields = {
        "method_kind": "Single",
        "method_binding_id": binding_id,
        "parent_scope_ref": "task_scope_001",
        "bind_reason": "compose",
    }
    fields.update(overrides)
    command = BindMethodCommand(**fields)
    return service.bind(COLLET_ID, command, make_actor())


def compose(
    service: MethodLifecycleService, parent_id: str, child_id: str, **overrides
):
    """经 ``compose`` 建一条 nest 边;字段可覆盖。"""
    fields = {
        "relation": "nest",
        "child_binding_id": child_id,
        "child_method_kind": "Single",
    }
    fields.update(overrides)
    command = ComposeMethodCommand(**fields)
    return service.compose(COLLET_ID, parent_id, command, make_actor())


AXES = current_axes()


class TestLifecycleTransitions:
    def test_happy_path_increments_version_and_audits(self):
        """BOUND→ACTIVE→SUSPENDED→ACTIVE→TERMINATED 全链:version +1/步,
        审计四字段(§6.3)在对象上覆写。"""
        store, _, service = make_service()
        bound = bind_nested(service, "mb_nest_1")
        assert bound.binding["state"] == "BOUND"
        assert bound.binding["version"] == 1

        activated = service.activate(
            COLLET_ID,
            "mb_nest_1",
            MethodStateChangeCommand(reason="开始执行"),
            make_actor(),
        )
        assert (activated.accepted, activated.state, activated.version) == (
            True,
            "ACTIVE",
            2,
        )
        suspended = service.suspend(
            COLLET_ID,
            "mb_nest_1",
            MethodStateChangeCommand(reason="暂停"),
            make_actor(),
        )
        assert (suspended.state, suspended.version) == ("SUSPENDED", 3)
        resumed = service.resume(
            COLLET_ID,
            "mb_nest_1",
            MethodStateChangeCommand(reason="恢复"),
            make_actor(),
        )
        assert (resumed.state, resumed.version) == ("ACTIVE", 4)
        terminated = service.terminate(
            COLLET_ID,
            "mb_nest_1",
            MethodStateChangeCommand(reason="收尾"),
            make_actor(),
        )
        assert (terminated.state, terminated.version) == ("TERMINATED", 5)

        binding = store.get_binding(AXES, "mb_nest_1", COLLET_ID)
        assert binding.state is BindingState.TERMINATED
        assert binding.transitioned_from == "ACTIVE"
        assert binding.transition_reason == "收尾"
        assert binding.transitioned_by == PRINCIPAL_ID
        assert binding.transitioned_at  # ISO 时间戳非空

    def test_terminated_then_activate_rejected(self):
        store, _, service = make_service()
        bind_nested(service, "mb_nest_a")
        service.terminate(
            COLLET_ID, "mb_nest_a", MethodStateChangeCommand(), make_actor()
        )
        with pytest.raises(ContextCollabValidationError, match="method.activate"):
            service.activate(
                COLLET_ID, "mb_nest_a", MethodStateChangeCommand(), make_actor()
            )
        # 终态对象未被改写:仍是 TERMINATED / version 不变。
        binding = store.get_binding(AXES, "mb_nest_a", COLLET_ID)
        assert (binding.state, binding.version) == (BindingState.TERMINATED, 2)

    def test_repeated_activate_idempotently_rejected(self):
        """§8.3:重复 activate 幂等拒绝(ACTIVE 无 method.activate 出边)。"""
        _, _, service = make_service()
        bind_nested(service, "mb_nest_b")
        service.activate(
            COLLET_ID, "mb_nest_b", MethodStateChangeCommand(), make_actor()
        )
        with pytest.raises(ContextCollabValidationError, match="method.activate"):
            service.activate(
                COLLET_ID, "mb_nest_b", MethodStateChangeCommand(), make_actor()
            )

    def test_update_config_optimistic_version_and_shape(self):
        store, _, service = make_service()
        bind_nested(service, "mb_cfg_1")
        updated = service.update_config(
            COLLET_ID,
            "mb_cfg_1",
            UpdateBindingConfigCommand(
                config={"max_compose_depth": 4}, expected_version=1
            ),
            make_actor(),
        )
        assert (updated.accepted, updated.state, updated.version) == (
            True,
            "BOUND",
            2,
        )
        assert store.get_binding(AXES, "mb_cfg_1", COLLET_ID).config == {
            "max_compose_depth": 4,
        }
        # 陈旧版本号:409 语义冲突。
        with pytest.raises(ContextCollabVersionConflict):
            service.update_config(
                COLLET_ID,
                "mb_cfg_1",
                UpdateBindingConfigCommand(config={}, expected_version=1),
                make_actor(),
            )
        # 非法 config 形态(max_compose_depth 越界):校验拒绝,不落半态。
        with pytest.raises(ContextCollabValidationError, match="max_compose_depth"):
            service.update_config(
                COLLET_ID,
                "mb_cfg_1",
                UpdateBindingConfigCommand(
                    config={"max_compose_depth": 99}, expected_version=2
                ),
                make_actor(),
            )
        assert store.get_binding(AXES, "mb_cfg_1", COLLET_ID).version == 2


class TestBindIdempotency:
    def test_bind_idempotent_same_shape_conflict_different_shape(self):
        """RF#3:同 ID 同形态返回已存在对象;同 ID 异形态 409 语义冲突。"""
        store, _, service = make_service()
        command = BindMethodCommand(
            method_kind="Group",
            method_binding_id="mb_fixed",
            parent_scope_ref="task_scope_001",
            bind_reason="compose",
        )
        first = service.bind(COLLET_ID, command, make_actor())
        assert first.binding["state"] == "BOUND"
        second = service.bind(COLLET_ID, command, make_actor())
        assert second.binding == first.binding
        assert len(store.list_bindings(AXES, COLLET_ID)) == 1
        with pytest.raises(ContextCollabValidationError):
            service.bind(
                COLLET_ID,
                BindMethodCommand(
                    method_kind="Group",
                    method_binding_id="mb_fixed",
                    parent_scope_ref="task_scope_002",
                ),
                make_actor(),
            )

    def test_bind_without_id_generates_prefix(self):
        _, _, service = make_service()
        result = service.bind(
            COLLET_ID,
            BindMethodCommand(method_kind="Swarm", parent_scope_ref="task_scope_003"),
            make_actor(),
        )
        assert result.binding["method_binding_id"].startswith("mb_")
        assert result.binding["bind_reason"] == "compose"


class TestComposeAndDetach:
    def test_compose_rejects_cycle_and_depth_overrun(self):
        """RF#4:全图环检测拒绝;深度超过 ``effective_max_compose_depth``
        (缺省 8)拒绝,恰好到边数的链仍放行。"""
        store, _, service = make_service()
        bind_nested(service, "mb_cyc_a")
        bind_nested(service, "mb_cyc_b")
        service.activate(
            COLLET_ID, "mb_cyc_a", MethodStateChangeCommand(), make_actor()
        )
        service.activate(
            COLLET_ID, "mb_cyc_b", MethodStateChangeCommand(), make_actor()
        )
        edge = compose(service, "mb_cyc_a", "mb_cyc_b")
        assert edge.edge["state"] == "ACTIVE"
        assert edge.edge["relation_kind"] == "nest"
        # A→B 已活跃,B→A 成环:拒绝。
        with pytest.raises(ContextCollabValidationError, match="cycle"):
            compose(service, "mb_cyc_b", "mb_cyc_a")

        # 缺省 cap 8:第 8 条边放行,第 9 条(深度 9)拒绝。
        store_d, _, service_d = make_service()
        ids = [f"mb_chain_{i}" for i in range(10)]
        for binding_id in ids:
            bind_nested(service_d, binding_id)
        for binding_id in ids[:9]:
            service_d.activate(
                COLLET_ID, binding_id, MethodStateChangeCommand(), make_actor()
            )
        for i in range(8):
            accepted = compose(service_d, ids[i], ids[i + 1])
            assert accepted.edge["state"] == "ACTIVE"
        with pytest.raises(ContextCollabValidationError, match="max_compose_depth"):
            service_d.compose(
                COLLET_ID,
                ids[8],
                ComposeMethodCommand(
                    relation="nest", child_binding_id=ids[9], child_method_kind="Single"
                ),
                make_actor(),
            )
        assert store_d.get_composition_edge(AXES, "edge_over", COLLET_ID) is None

    def test_compose_rejects_non_active_parent(self):
        """spec §4 通用规则:binding 非 ACTIVE 拒绝副作用(compose 为副作用)。"""
        _, _, service = make_service()
        # 父 SUSPENDED 拒绝。
        bind_nested(service, "mb_p_susp")
        bind_nested(service, "mb_child_susp")
        service.activate(
            COLLET_ID, "mb_p_susp", MethodStateChangeCommand(), make_actor()
        )
        service.suspend(
            COLLET_ID, "mb_p_susp", MethodStateChangeCommand(), make_actor()
        )
        with pytest.raises(ContextCollabValidationError, match="ACTIVE"):
            compose(service, "mb_p_susp", "mb_child_susp")
        # 父 TERMINATED 拒绝(terminated 后出边创建一律拒绝)。
        bind_nested(service, "mb_p_term")
        bind_nested(service, "mb_child_term")
        service.activate(
            COLLET_ID, "mb_p_term", MethodStateChangeCommand(), make_actor()
        )
        service.terminate(
            COLLET_ID, "mb_p_term", MethodStateChangeCommand(), make_actor()
        )
        with pytest.raises(ContextCollabValidationError, match="ACTIVE"):
            compose(service, "mb_p_term", "mb_child_term")

    def test_compose_matrix_violation_rejected(self):
        _, _, service = make_service()
        bind_nested(service, "mb_sw_parent")
        bind_nested(service, "mb_sw_child")
        service.activate(
            COLLET_ID, "mb_sw_parent", MethodStateChangeCommand(), make_actor()
        )
        with pytest.raises(ContextCollabValidationError):
            service.compose(
                COLLET_ID,
                "mb_sw_parent",
                ComposeMethodCommand(
                    relation="escalate",
                    child_binding_id="mb_sw_child",
                    child_method_kind="Swarm",
                ),
                make_actor(),
            )

    def test_detach_flips_detached_and_keeps_audit_visibility(self):
        store, _, service = make_service()
        bind_nested(service, "mb_det_p")
        bind_nested(service, "mb_det_c")
        service.activate(
            COLLET_ID, "mb_det_p", MethodStateChangeCommand(), make_actor()
        )
        compose(service, "mb_det_p", "mb_det_c", edge_id="edge_det_1")
        result = service.detach(
            COLLET_ID,
            "edge_det_1",
            MethodStateChangeCommand(reason="修剪"),
            make_actor(),
        )
        assert result.edge["state"] == "DETACHED"
        assert result.edge["transitioned_from"] == "ACTIVE"
        assert result.edge["transition_reason"] == "修剪"
        # active 默认过滤不可见;显式关闭过滤可见(审计保留)。
        assert store.list_composition_edges(AXES, COLLET_ID) == []
        edges = store.list_composition_edges(AXES, COLLET_ID, active_only=False)
        assert [edge.edge_id for edge in edges] == ["edge_det_1"]
        assert edges[0].state.value == "DETACHED"


class TestAuthorizationSeam:
    def test_unauthorized_stranger_rejected(self):
        """经真 ``StructuralMethodOpsAuthorizer``:陌生 actor 治理面全拒。"""
        _, _, service = make_service()
        stranger = make_actor("u_9999")
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.bind(
                COLLET_ID,
                BindMethodCommand(
                    method_kind="Single", parent_scope_ref="task_scope_009"
                ),
                stranger,
            )
        # owner 先建并激活,随后陌生人对同一 binding 操作仍拒绝。
        owner_result = service.bind(
            COLLET_ID,
            BindMethodCommand(
                method_kind="Single",
                method_binding_id="mb_owned",
                parent_scope_ref="task_scope_009",
            ),
            make_actor(),
        )
        assert owner_result.binding["state"] == "BOUND"
        service.activate(
            COLLET_ID, "mb_owned", MethodStateChangeCommand(), make_actor()
        )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.terminate(
                COLLET_ID, "mb_owned", MethodStateChangeCommand(), stranger
            )

    def test_inspect_read_face_allows_authenticated_stranger(self):
        """inspect 是读面投影:任意已认证 actor 放行(比照 GET status)。"""
        _, _, service = make_service()
        bind_nested(service, "mb_read_1")
        result = service.inspect(COLLET_ID, "mb_read_1", make_actor("u_reader_77"))
        assert result.binding["method_binding_id"] == "mb_read_1"


class TestTerminateTopLevelGate:
    def _projected_top_level_service(self, status: str):
        store = InMemoryMethodStore()
        repo = _FakeCollabRepository({})
        repo.set_status(COLLET_ID, status)
        service = MethodLifecycleService(
            store=store,
            authorizer=StructuralMethodOpsAuthorizer(),
            collab_repo=repo,
            product_catalog=PRODUCT_CATALOG,
        )
        record = repo.get_current_status(COLLET_ID)
        project_created_context(
            store,
            AXES,
            COLLET_ID,
            record.context_spec,
            make_actor(),
            now=NOW,
            catalog=PRODUCT_CATALOG,
        )
        return store, repo, service

    def test_rejected_on_live_collet_status(self):
        _, repo, service = self._projected_top_level_service("in_progress")
        binding_id = top_level_binding_id(COLLET_ID)
        with pytest.raises(ContextCollabValidationError, match="task.cancelled"):
            service.terminate(
                COLLET_ID, binding_id, MethodStateChangeCommand(), make_actor()
            )

    def test_allowed_on_task_cancelled(self):
        store, _, service = self._projected_top_level_service("cancelled")
        binding_id = top_level_binding_id(COLLET_ID)
        result = service.terminate(
            COLLET_ID, binding_id, MethodStateChangeCommand(), make_actor()
        )
        assert result.state == "TERMINATED"
        binding = store.get_binding(AXES, binding_id, COLLET_ID)
        assert binding.state is BindingState.TERMINATED

    def test_allowed_on_task_accepted(self):
        store, _, service = self._projected_top_level_service("done")
        binding_id = top_level_binding_id(COLLET_ID)
        result = service.terminate(
            COLLET_ID, binding_id, MethodStateChangeCommand(), make_actor()
        )
        assert result.state == "TERMINATED"
        assert store.get_binding(AXES, binding_id, COLLET_ID).state is (
            BindingState.TERMINATED
        )

    def test_suspend_resume_allowed_but_terminate_gated_on_live_status(self):
        """fix round 1:门槛只约束 terminate——活上下文上 suspend/resume
        是常规生命周期治理,照常放行;terminate 仍被拒。"""
        store, _, service = self._projected_top_level_service("in_progress")
        binding_id = top_level_binding_id(COLLET_ID)
        suspended = service.suspend(
            COLLET_ID,
            binding_id,
            MethodStateChangeCommand(reason="暂停"),
            make_actor(),
        )
        assert (suspended.state, suspended.version) == ("SUSPENDED", 2)
        resumed = service.resume(
            COLLET_ID,
            binding_id,
            MethodStateChangeCommand(reason="恢复"),
            make_actor(),
        )
        assert (resumed.state, resumed.version) == ("ACTIVE", 3)
        assert store.get_binding(AXES, binding_id, COLLET_ID).state is (
            BindingState.ACTIVE
        )
        with pytest.raises(ContextCollabValidationError, match="task.cancelled"):
            service.terminate(
                COLLET_ID, binding_id, MethodStateChangeCommand(), make_actor()
            )

    def test_nested_binding_terminate_bypasses_gate(self):
        """嵌套绑定(bind_reason=compose)无门槛:活上下文上可终止。"""
        store, _, service = self._projected_top_level_service("in_progress")
        bind_nested(service, "mb_nested_free")
        result = service.terminate(
            COLLET_ID, "mb_nested_free", MethodStateChangeCommand(), make_actor()
        )
        assert result.state == "TERMINATED"


class TestTopLevelProjection:
    def test_project_created_context_binds_active_top_level(self):
        store = InMemoryMethodStore()
        repo = _FakeCollabRepository({COLLET_ID: _status_record()})
        record = repo.get_current_status(COLLET_ID)
        binding = project_created_context(
            store,
            AXES,
            COLLET_ID,
            record.context_spec,
            make_actor(),
            now=NOW,
            catalog=PRODUCT_CATALOG,
        )
        # spec §3.2 初始状态作者裁决:顶层投影直接 ACTIVE;§9.2 单向派生。
        assert binding.state is BindingState.ACTIVE
        assert binding.version == 1
        assert binding.bind_reason == "create_context"
        assert binding.parent_scope_ref is None
        assert binding.method_kind == "Single"
        assert binding.method_names == ("Single",)
        assert binding.profile_versions == {"Single": "0.2"}
        assert binding.participant_ref == EXECUTOR_ID
        assert binding.transition_reason == "top-level projection"
        assert binding.transitioned_by == PRINCIPAL_ID
        # 重复投影幂等返回既有对象,不产生第二行。
        again = project_created_context(
            store,
            AXES,
            COLLET_ID,
            record.context_spec,
            make_actor(),
            now="2026-11-12T09:30:00+00:00",
            catalog=PRODUCT_CATALOG,
        )
        assert again.method_binding_id == binding.method_binding_id
        assert len(store.list_bindings(AXES, COLLET_ID)) == 1

    def test_project_open_group_names_projection(self):
        store = InMemoryMethodStore()
        repo = _FakeCollabRepository(
            {COLLET_ID: _status_record(method_kind="Open + Group")}
        )
        record = repo.get_current_status(COLLET_ID)
        binding = project_created_context(
            store,
            AXES,
            COLLET_ID,
            record.context_spec,
            make_actor(),
            now=NOW,
            catalog=PRODUCT_CATALOG,
        )
        assert binding.method_names == ("Open", "Group")
        assert binding.profile_versions == {"Open": "0.2", "Group": "0.2"}
        # 公开讨论无 executor:participant_ref 不虚造。
        assert binding.participant_ref == EXECUTOR_ID


class TestSelfHeal:
    def test_ensure_top_level_binding_rebuilds_and_is_idempotent(self):
        """§6.4 读路径自愈:store 归零后按 context_spec 静默重建 ACTIVE 投影;
        重复调用幂等;collet 不存在则 NotFound。"""
        store = InMemoryMethodStore()
        repo = _FakeCollabRepository({COLLET_ID: _status_record()})
        healed = ensure_top_level_binding(
            store, AXES, COLLET_ID, repo, make_actor(), now=NOW, catalog=PRODUCT_CATALOG
        )
        assert healed.state is BindingState.ACTIVE
        assert healed.bind_reason == "create_context_heal"
        assert healed.method_kind == "Single"
        assert healed.method_names == ("Single",)
        assert len(store.list_bindings(AXES, COLLET_ID)) == 1
        again = ensure_top_level_binding(
            store, AXES, COLLET_ID, repo, make_actor(), now=NOW, catalog=PRODUCT_CATALOG
        )
        assert again.method_binding_id == healed.method_binding_id
        assert len(store.list_bindings(AXES, COLLET_ID)) == 1
        with pytest.raises(ContextCollabNotFoundError):
            ensure_top_level_binding(
                store,
                AXES,
                "collet_missing_001",
                repo,
                make_actor(),
                now=NOW,
                catalog=PRODUCT_CATALOG,
            )

    def test_ensure_returns_existing_without_rebuild(self):
        store = InMemoryMethodStore()
        repo = _FakeCollabRepository({COLLET_ID: _status_record()})
        record = repo.get_current_status(COLLET_ID)
        projected = project_created_context(
            store,
            AXES,
            COLLET_ID,
            record.context_spec,
            make_actor(),
            now=NOW,
            catalog=PRODUCT_CATALOG,
        )
        healed = ensure_top_level_binding(
            store, AXES, COLLET_ID, repo, make_actor(), now=NOW, catalog=PRODUCT_CATALOG
        )
        # 顶层已存在:直接返回权威对象,不重建、不改审计。
        assert healed.method_binding_id == projected.method_binding_id
        assert healed.transition_reason == "top-level projection"

    def test_inspect_rebuilds_missing_top_level_binding(self):
        """fix round 1:inspect 读路径接通自愈(spec §6.4)——store 未命中
        确定性顶层 ID 时重建 ACTIVE 投影后照常返回;非顶层缺 ID 照旧 NotFound。"""
        store = InMemoryMethodStore()
        repo = _FakeCollabRepository({COLLET_ID: _status_record()})
        service = MethodLifecycleService(
            store=store,
            authorizer=StructuralMethodOpsAuthorizer(),
            collab_repo=repo,
            product_catalog=PRODUCT_CATALOG,
        )
        result = service.inspect(
            COLLET_ID, top_level_binding_id(COLLET_ID), make_actor()
        )
        assert result.binding["bind_reason"] == "create_context_heal"
        assert result.state_summary["state"] == "ACTIVE"
        assert len(store.list_bindings(AXES, COLLET_ID)) == 1
        again = service.inspect(
            COLLET_ID, top_level_binding_id(COLLET_ID), make_actor()
        )
        assert again.binding == result.binding
        assert len(store.list_bindings(AXES, COLLET_ID)) == 1


class TestInspectionProjection:
    def test_inspect_returns_summary_capabilities_and_evidence(self):
        store, _, service = make_service()
        bind_nested(service, "mb_ins_child")
        bind_nested(service, "mb_ins_parent")
        service.activate(
            COLLET_ID, "mb_ins_parent", MethodStateChangeCommand(), make_actor()
        )
        compose(service, "mb_ins_parent", "mb_ins_child")
        result = service.inspect(COLLET_ID, "mb_ins_child", make_actor())
        assert result.binding["method_binding_id"] == "mb_ins_child"
        assert result.state_summary == {
            "state": "BOUND",
            "version": 1,
            "composition_edges_active": 1,
        }
        single_profile = result.capabilities["Single"]
        assert single_profile["profile_version"] == "0.2"
        assert single_profile["participation_model"] == "selected"
        assert "single.selection.select" in single_profile["capabilities"]
        evidence = result.conformance_evidence
        assert evidence["machines_valid"] is True
        assert evidence["binding_state"] == "BOUND"
        assert evidence["audit"]["transitioned_from"] == ""
        assert evidence["audit"]["transitioned_at"]
        # 只读:对象未被改写。
        binding = store.get_binding(AXES, "mb_ins_child", COLLET_ID)
        assert (binding.state, binding.version) == (BindingState.BOUND, 1)

    def test_inspect_missing_binding_not_found(self):
        _, _, service = make_service()
        with pytest.raises(ContextCollabNotFoundError):
            service.inspect(COLLET_ID, "mb_missing_001", make_actor())


class TestProtocolConformance:
    def test_service_satisfies_protocol(self):
        from agentclaw.community.core.task.context_collab.method_service_api import (
            MethodLifecycleServiceProtocol,
        )

        _, _, service = make_service()
        assert isinstance(service, MethodLifecycleServiceProtocol)
