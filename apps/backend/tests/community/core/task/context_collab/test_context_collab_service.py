"""Context collab 服务创建用例单测(Task 7)。

覆盖 ``ContextCollabService.create_context``(spec §6.2 指定执行请求
示例)、输入校验(未知枚举 / 空值 / 缺 executor)、幂等重放
与结构授权器(§13:Single 必须有
executor、append 身份、visibility 过滤)。仓储用手写 in-memory fake,
不触碰数据库;真实仓储实现由 Task 8 提供。
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    ContextCollabAuthorizerProtocol,
    StructuralContextCollabAuthorizer,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ColletState,
    ContextSpec,
    CurrentSnapshot,
    ProgressSnapshot,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    TaskStatus,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepositoryProtocol,
    ContextCreatedRecord,
    CreateContextRecordRequest,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
    ContextCollabCreatedResult,
    ContextCollabServiceProtocol,
    ContextCreateCommand,
)
from agentclaw.community.core.task.context_collab.services.context_collab_service import (
    ContextCollabService,
)
from agentclaw.community.core.task.context_collab.services.method_lifecycle_service import (
    current_axes,
)

OBJECTIVE = "整理 Context 协作协议评审结论并提交可评审 PR"
PRINCIPAL = PrincipalRef(principal_type="human", principal_id="u_1001")
EXECUTOR = AgentRef(agent_type="bot", agent_id="bot_coding_001")


def make_create_command(**overrides) -> ContextCreateCommand:
    """用 spec §6.2 指定执行请求示例构造创建命令;可按字段覆盖。"""
    fields = {
        "space": SpaceRef(space_id="space_workspace_001", environment="prod"),
        "principal_ref": PRINCIPAL,
        "product_kind": "team_todo",
        "workflow_kind": "team_todo_execution",
        "method_kind": "Single",
        "executor_ref": EXECUTOR,
        "objective": OBJECTIVE,
        "resource_refs": [
            ResourceRef(
                resource_type="forum_topic", resource_id="topic_001", revision="3"
            )
        ],
    }
    fields.update(overrides)
    return ContextCreateCommand(**fields)


def make_actor(principal_id: str = "u_1001") -> ActorContext:
    return ActorContext(actor=PrincipalRef("human", principal_id), source="api")


class _FakeContextCollabRepository:

    """手写 in-memory 仓储 fake,只实现 create_context 的 P1 语义。

    创建(不设幂等键,重复调用即重复创建):生成 ``collet_`` / ``evt_`` ID、
    初始快照(status=created、progress.percent=0、current_event_id=None)、
    初始 task.created 事件(parent=None)。
    真实仓储见 persistence/repository.py。
    """

    def list_collets_by_product(self, product_kind: str):
        raise NotImplementedError("fake does not implement product listing")

    def count_message_events(self, collab_context_ids):
        raise NotImplementedError("fake does not implement message counting")


    def __init__(self) -> None:
        self.create_calls: list[CreateContextRecordRequest] = []

    def create_context(self, request: CreateContextRecordRequest) -> ContextCreatedRecord:
        self.create_calls.append(request)

        collab_context_id = "collet_" + uuid4().hex
        event_id = "evt_" + uuid4().hex
        snapshot = CurrentSnapshot(
            objective=request.context_spec.objective,
            status=TaskStatus.CREATED.value,
            principal_ref=request.context_spec.principal_ref,
            executor_ref=request.context_spec.executor_ref,
            method_kind=request.context_spec.method_kind,
            progress=ProgressSnapshot(percent=0, current_event_id=None),
        )
        collet = Collet(
            collet_id=collab_context_id,
            initiator_ref=request.initial_event.actor_ref,
            state=ColletState.ACTIVE,
            scope=request.context_spec.space,
            current_event_id=event_id,
            version=1,
        )
        initial_event = EventRecord(
            collab_context_id=collab_context_id,
            event_id=event_id,
            parent_event=None,
            event_kind=request.initial_event.event_kind,
            primitive_type=request.initial_event.primitive_type,
            actor_ref=request.initial_event.actor_ref,
            visibility="public",
            payload=dict(request.initial_event.payload),
            context_version_after=1,
            occurred_at=datetime.now(timezone.utc),
        )
        return ContextCreatedRecord(
            collet=collet,
            context_spec=request.context_spec,
            current_snapshot=snapshot,
            initial_event=initial_event,
        )

    def append_event(self, collab_context_id, request):
        raise NotImplementedError("fake only implements create_context")

    def get_current_status(self, collab_context_id, *, trunk_events):
        raise NotImplementedError("fake only implements create_context")


class TestCreateContext:
    def test_creates_collet_with_initial_event(self):
        repo = _FakeContextCollabRepository()
        assert isinstance(repo, ContextCollabRepositoryProtocol)
        service = ContextCollabService(
            method_store=InMemoryMethodStore(),
            repository=repo,
        )
        actor = make_actor()

        result = service.create_context(make_create_command(), actor)

        assert isinstance(result, ContextCollabCreatedResult)
        assert result.collab_context_id.startswith("collet_")
        assert result.context_version == 1
        assert result.current_snapshot["objective"] == OBJECTIVE
        assert result.current_snapshot["status"] == "created"
        assert result.current_snapshot["method_kind"] == "Single"
        assert result.current_snapshot["principal_ref"] == "u_1001"
        assert result.current_snapshot["executor_ref"] == "bot_coding_001"
        assert result.current_snapshot["progress"]["percent"] == 0
        current_event_id = result.current_snapshot["progress"]["current_event_id"]
        assert isinstance(current_event_id, str)
        assert current_event_id.startswith("evt_")
        assert result.current_event["event_id"] == current_event_id
        assert result.current_event["event_kind"] == "task.created"
        assert result.current_event["primitive_type"] == "Task"
        assert result.current_event["parent_event"] is None

        # 服务传给仓储的创建请求语义(§6.2 / 初始事件)。
        request = repo.create_calls[0]
        assert isinstance(request, CreateContextRecordRequest)
        assert request.context_spec.method_kind == "Single"
        assert request.context_spec.executor_ref == EXECUTOR
        assert request.initial_event.event_kind == "task.created"
        assert request.initial_event.primitive_type == "Task"
        assert request.initial_event.actor_ref == actor.actor
        # 创建事实全量入初始事件 payload(事件承载,spec
        # 2026-11-12-context-collab-l4-creation-event-facts):objective/
        # resource_refs 第一天就有;本轮补 method_kind/executor_ref/
        # product_kind/workflow_kind;space/principal_ref 由聚合持久不重复。
        assert request.initial_event.payload == {
            "objective": OBJECTIVE,
            "resource_refs": [
                {
                    "resource_type": "forum_topic",
                    "resource_id": "topic_001",
                    "revision": "3",
                }
            ],
            "method_kind": "Single",
            "executor_ref": {
                "agent_type": "bot",
                "agent_id": "bot_coding_001",
            },
            "product_kind": "team_todo",
            "workflow_kind": "team_todo_execution",
        }

    def test_create_without_resource_refs_still_carries_empty_key(self):
        """空引用恒带空列表 key(spec resource-binding §2):新行与存量行
        (payload 无 key)判别有据。"""
        repo = _FakeContextCollabRepository()
        service = ContextCollabService(
            method_store=InMemoryMethodStore(),
            repository=repo,
        )

        service.create_context(
            make_create_command(resource_refs=[]), make_actor()
        )

        request = repo.create_calls[0]
        assert request.initial_event.payload["resource_refs"] == []

    def test_single_without_executor_rejected(self):
        service = ContextCollabService(
            method_store=InMemoryMethodStore(),
            repository=_FakeContextCollabRepository(),
        )
        with pytest.raises(ContextCollabValidationError):
            service.create_context(
                make_create_command(executor_ref=None), make_actor()
            )

    @pytest.mark.parametrize(
        "overrides",
        [
            {"product_kind": "os"},
            {"workflow_kind": "unknown"},
            {"method_kind": "swarm"},
            {"objective": ""},
        ],
        ids=[
            "unknown-product-kind",
            "unknown-workflow-kind",
            "unknown-method-kind",
            "empty-objective",
        ],
    )
    def test_invalid_create_payload_rejected(self, overrides):
        service = ContextCollabService(
            method_store=InMemoryMethodStore(),
            repository=_FakeContextCollabRepository(),
        )
        with pytest.raises(ContextCollabValidationError):
            service.create_context(make_create_command(**overrides), make_actor())

    def test_service_satisfies_protocol(self):
        service = ContextCollabService(
            method_store=InMemoryMethodStore(),
            repository=_FakeContextCollabRepository(),
        )
        assert isinstance(service, ContextCollabServiceProtocol)

    def test_create_rejects_bare_group_swarm_top_level(self):
        """顶层白名单 = Single / Open + Group / Open(task_commission spec
        裁决 B);裸 Group / Swarm 经嵌套 bind 进入,直接创建拒绝。"""
        service = ContextCollabService(
            method_store=InMemoryMethodStore(),
            repository=_FakeContextCollabRepository(),
        )
        for method_kind in ("Group", "Swarm"):
            with pytest.raises(
                ContextCollabValidationError, match="top-level"
            ):
                service.create_context(
                    make_create_command(method_kind=method_kind), make_actor()
                )
        # 白名单内产品形态照常通过 G1 create 裁决(含 2026-11-13 新入的
        # 顶层单值 Open——任务代办产品的方法形态,spec 裁决 B)。
        service.create_context(
            make_create_command(method_kind="Single"), make_actor()
        )
        service.create_context(
            make_create_command(method_kind="Open + Group"), make_actor()
        )
        # 裸 "Open" 顶层放行:任务代办产品的协作形态。
        service.create_context(
            make_create_command(method_kind="Open"), make_actor()
        )


class _FixedIdContextCollabRepository(_FakeContextCollabRepository):
    """创建恒返回固定 collab_context_id 的 fake;用于顶层投影幂等用例的
    同命令重放(真实仓储创建不设幂等键,重放语义由 store 幂等承载)。"""

    def create_context(self, request: CreateContextRecordRequest) -> ContextCreatedRecord:
        self.create_calls.append(request)
        collab_context_id = "collet_projected_001"
        event_id = "evt_projected_001"
        snapshot = CurrentSnapshot(
            objective=request.context_spec.objective,
            status=TaskStatus.CREATED.value,
            principal_ref=request.context_spec.principal_ref,
            executor_ref=request.context_spec.executor_ref,
            method_kind=request.context_spec.method_kind,
            progress=ProgressSnapshot(percent=0, current_event_id=None),
        )
        collet = Collet(
            collet_id=collab_context_id,
            initiator_ref=request.initial_event.actor_ref,
            state=ColletState.ACTIVE,
            scope=request.context_spec.space,
            current_event_id=event_id,
            version=1,
        )
        initial_event = EventRecord(
            collab_context_id=collab_context_id,
            event_id=event_id,
            parent_event=None,
            event_kind=request.initial_event.event_kind,
            primitive_type=request.initial_event.primitive_type,
            actor_ref=request.initial_event.actor_ref,
            visibility="public",
            payload=dict(request.initial_event.payload),
            context_version_after=1,
            occurred_at=datetime.now(timezone.utc),
        )
        return ContextCreatedRecord(
            collet=collet,
            context_spec=request.context_spec,
            current_snapshot=snapshot,
            initial_event=initial_event,
        )


class TestTopLevelProjection:
    """create_context 成功路径的顶层 MethodBinding 投影(spec §9.2)。"""

    def test_create_projects_top_level_binding(self):
        store = InMemoryMethodStore()
        service = ContextCollabService(
            method_store=store,
            repository=_FixedIdContextCollabRepository(),
        )
        created = service.create_context(make_create_command(), make_actor())
        bindings = store.list_bindings(current_axes(), created.collab_context_id)
        assert len(bindings) == 1
        binding = bindings[0]
        assert binding.state is BindingState.ACTIVE
        assert binding.bind_reason == "create_context"
        assert binding.method_kind == "Single"
        assert binding.method_names == ("Single",)
        assert binding.participant_ref == "bot_coding_001"

        # 同命令重复创建(同 ID):顶层投影幂等,store 仍只一行。
        again = service.create_context(make_create_command(), make_actor())
        assert again.collab_context_id == created.collab_context_id
        assert len(store.list_bindings(current_axes(), created.collab_context_id)) == 1
        rebound = store.list_bindings(current_axes(), created.collab_context_id)[0]
        assert rebound.transition_reason == "top-level projection"

    def test_create_projects_open_group_method_names(self):
        store = InMemoryMethodStore()
        service = ContextCollabService(
            method_store=store,
            repository=_FixedIdContextCollabRepository(),
        )
        created = service.create_context(
            make_create_command(method_kind="Open + Group", executor_ref=None),
            make_actor(),
        )
        binding = store.list_bindings(current_axes(), created.collab_context_id)[0]
        # 复合 wire 值投影为核心方法元组(spec §3.1),不能自由组合。
        assert binding.method_names == ("Open", "Group")
        assert binding.profile_versions == {"Open": "0.2", "Group": "0.2"}
        # 公开讨论无 executor:participant_ref 不虚造。
        assert binding.participant_ref is None


class _RecordingAuthorizer(StructuralContextCollabAuthorizer):
    """记录 check_create 调用以验证授权接缝可替换。"""

    def __init__(self) -> None:
        self.check_create_calls = []

    def check_create(self, spec, actor) -> None:
        self.check_create_calls.append((spec, actor))
        super().check_create(spec, actor)


class TestAuthorizerSeam:
    def test_default_authorizer_is_structural(self):
        service = ContextCollabService(
            method_store=InMemoryMethodStore(),
            repository=_FakeContextCollabRepository(),
        )
        assert isinstance(
            service, ContextCollabServiceProtocol
        ) and isinstance(service._authorizer, StructuralContextCollabAuthorizer)

    def test_injected_authorizer_checked_on_create(self):
        authorizer = _RecordingAuthorizer()
        service = ContextCollabService(
            method_store=InMemoryMethodStore(),
            repository=_FakeContextCollabRepository(),
            authorizer=authorizer,
        )
        assert isinstance(authorizer, ContextCollabAuthorizerProtocol)
        actor = make_actor()
        service.create_context(make_create_command(), actor)
        assert len(authorizer.check_create_calls) == 1
        spec, checked_actor = authorizer.check_create_calls[0]
        assert spec.method_kind == "Single"
        assert checked_actor is actor


def _fake_spec() -> ContextSpec:
    """构造带 principal/executor 的创建参数,供授权器身份测试用。"""
    return ContextSpec(
        space=SpaceRef(space_id="space_workspace_001", environment="prod"),
        principal_ref=PrincipalRef("human", "u_1001"),
        method_kind="Single",
        executor_ref=AgentRef("bot", "bot_coding_001"),
        objective=OBJECTIVE,
        resource_refs=(),
        product_kind=None,
        workflow_kind=None,
    )


def _fake_collet() -> Collet:
    """构造六字段最小 Collet,供授权器身份测试用。"""
    return Collet(
        collet_id="collet_todo_001",
        initiator_ref=PrincipalRef("human", "u_1001"),
        state=ColletState.ACTIVE,
        scope=SpaceRef(space_id="space_workspace_001", environment="prod"),
        current_event_id=None,
        version=1,
    )


def _event(event_id: str, visibility: str) -> EventRecord:
    return EventRecord(
        collab_context_id="collet_todo_001",
        event_id=event_id,

        parent_event=None,
        event_kind="task.progress.updated",
        primitive_type="Activity",
        actor_ref=PRINCIPAL,
        visibility=visibility,
        payload={},
        context_version_after=1,
        occurred_at=datetime(2026, 11, 10, 8, 30, 0),
    )


class TestStructuralAuthorizer:
    def test_check_create_single_requires_executor(self):
        spec = ContextSpec(
            space=SpaceRef(space_id="space_workspace_001", environment="prod"),
            principal_ref=PRINCIPAL,
            method_kind="Single",
            executor_ref=None,
            objective=OBJECTIVE,
            resource_refs=(),
            product_kind=None,
            workflow_kind=None,
        )
        with pytest.raises(ContextCollabValidationError):
            StructuralContextCollabAuthorizer().check_create(
                spec, make_actor()
            )

    def test_check_append_requires_principal_or_executor(self):
        authorizer = StructuralContextCollabAuthorizer()
        collet = _fake_collet()
        spec = _fake_spec()
        # principal 通过
        authorizer.check_append(collet, spec, None, make_actor("u_1001"))
        # executor(agent_id 即 bot_coding_001)通过
        authorizer.check_append(collet, spec, None, make_actor("bot_coding_001"))
        # 陌生 actor 拒绝
        with pytest.raises(ContextCollabValidationError):
            authorizer.check_append(collet, spec, None, make_actor("u_9999"))

    def test_filter_visible_events_by_actor_role(self):
        authorizer = StructuralContextCollabAuthorizer()
        collet = _fake_collet()
        spec = _fake_spec()
        events = [_event("evt_pub", "public"),
                  _event("evt_ws", "workspace"),
                  _event("evt_priv", "private")]

        principal_view = authorizer.filter_visible_events(
            collet, spec, events, make_actor("u_1001"))
        assert [e.event_id for e in principal_view] == [
            "evt_pub", "evt_ws", "evt_priv",
        ]

        executor_view = authorizer.filter_visible_events(
            collet, spec, events, make_actor("bot_coding_001"))
        assert [e.event_id for e in executor_view] == ["evt_pub", "evt_ws"]

        outsider_view = authorizer.filter_visible_events(
            collet, spec, events, make_actor("u_9999"))
        assert [e.event_id for e in outsider_view] == ["evt_pub"]
