"""Context collab 服务创建用例单测(Task 7)。

覆盖 ``ContextCollabService.create_collet``(spec §6.2 指定执行请求
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
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    EventAppendCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    Task,
    TaskStatus,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.collaboration_creation import (
    ContextCollabService,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepositoryProtocol,
    ContextCreatedRecord,
    CreateColletRecordRequest,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
    CollaborationCreateCommand,
    ContextCollabServiceProtocol,
)

from tests.community.core.task.context_collab.creation_facts import creation_facts
from tests.community.core.task.context_collab.product_catalog import PRODUCT_CATALOG

OBJECTIVE = "整理 Context 协作协议评审结论并提交可评审 PR"
PRINCIPAL = PrincipalRef(principal_type="human", principal_id="u_1001")
EXECUTOR = AgentRef(agent_type="bot", agent_id="bot_coding_001")


def make_create_command(**overrides) -> CollaborationCreateCommand:
    """用 spec §6.2 指定执行请求示例构造创建命令;可按字段覆盖。"""
    fields = {
        "to_space": SpaceRef(space_id="space_workspace_001", environment="prod"),
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
    return CollaborationCreateCommand(**fields)


def make_actor(principal_id: str = "u_1001") -> ActorContext:
    return ActorContext(actor=PrincipalRef("human", principal_id), source="api")


class _FakeContextCollabRepository:
    """手写 in-memory 仓储 fake,只实现 create_collet 的 P1 语义。

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
        self.create_calls: list[CreateColletRecordRequest] = []
        self.created_records: list[ContextCreatedRecord] = []

    def create_collet(self, request: CreateColletRecordRequest) -> ContextCreatedRecord:
        self.create_calls.append(request)

        collab_context_id = "collet_" + uuid4().hex
        event_id = "evt_" + uuid4().hex
        task = Task(
            objective=request.objective,
            status=TaskStatus.CREATED.value,
            principal_ref=request.principal_ref,
            executor_refs=(
                (request.executor_ref,) if request.executor_ref is not None else ()
            ),
            task_id="task_" + collab_context_id,
            collet_ref=collab_context_id,
            version=1,
            parent_task_ref=None,
            completion_conditions=(),
            created_event_ref=event_id,
            last_event_ref=event_id,
        )
        collet = Collet(
            collet_id=collab_context_id,
            initiator_ref=request.initial_event.actor_ref,
            state=ColletState.ACTIVE,
            scope=request.space,
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
        record = ContextCreatedRecord(
            collet=collet,
            creation_facts=request.creation_facts,
            task=task,
            initial_event=initial_event,
        )
        self.created_records.append(record)
        return record

    def append_event(self, collab_context_id, request):
        raise NotImplementedError("fake only implements create_collet")

    def get_current_status(self, collab_context_id, *, trunk_events):
        raise NotImplementedError("fake only implements create_collet")

    def create_l4_task(self, collab_context_id, command, actor_ref):
        raise NotImplementedError("fake does not implement Task creation")

    def create_l4_collet(self, command, actor_ref):
        raise NotImplementedError("application-only fake")

    def attach_product_instance(self, collab_context_id, **kwargs):
        raise NotImplementedError("application-only fake")


class TestCreateContext:
    def test_creates_collet_with_initial_event(self):
        repo = _FakeContextCollabRepository()
        assert isinstance(repo, ContextCollabRepositoryProtocol)
        service = ContextCollabService(
            method_store=InMemoryMethodStore(),
            repository=repo,
            product_catalog=PRODUCT_CATALOG,
        )
        actor = make_actor()

        result = service.create_collaboration(make_create_command(), actor)

        assert isinstance(result, Collet)
        record = repo.created_records[0]
        assert result is record.collet
        assert result.collet_id.startswith("collet_")
        assert result.version == 1
        assert result.initiator_ref == actor.actor
        assert result.scope == make_create_command().to_space
        assert result.state is ColletState.ACTIVE
        assert not hasattr(result, "current_event_id")
        assert record.initial_event.event_id.startswith("evt_")
        assert Collet.from_dict(result.to_dict()) == result
        # Work and creation facts remain separate from the returned Collet.
        assert record.task.objective == OBJECTIVE
        assert record.task.status == "created"
        assert record.task.principal_ref == PRINCIPAL
        assert record.task.executor_ref == EXECUTOR
        assert record.creation_facts["method_kind"] == "Single"
        assert record.initial_event.parent_event is None

        # 服务传给仓储的创建请求语义(§6.2 / 初始事件)。
        request = repo.create_calls[0]
        assert isinstance(request, CreateColletRecordRequest)
        assert request.method_kind == "Single"
        assert request.executor_ref == EXECUTOR
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
            "product_profile_ref": None,
        }

    def test_create_repository_failure_does_not_return_collet(self):
        error = RuntimeError("create write failed")

        class FailingRepository(_FakeContextCollabRepository):
            def create_collet(self, request):
                raise error

        class RecordingMethodStore(InMemoryMethodStore):
            def __init__(self):
                super().__init__()
                self.writes = []

            def upsert_binding(self, axes, binding):
                self.writes.append(binding)
                return super().upsert_binding(axes, binding)

        store = RecordingMethodStore()
        service = ContextCollabService(
            repository=FailingRepository(),
            method_store=store,
            product_catalog=PRODUCT_CATALOG,
        )
        with pytest.raises(RuntimeError) as failure:
            service.create_collaboration(make_create_command(), make_actor())
        assert failure.value is error
        assert store.writes == []

    def test_create_binding_failure_does_not_return_success(self):
        error = RuntimeError("binding write failed")

        class FailingMethodStore(InMemoryMethodStore):
            def upsert_binding(self, axes, binding):
                raise error

        repo = _FakeContextCollabRepository()
        service = ContextCollabService(
            repository=repo,
            method_store=FailingMethodStore(),
            product_catalog=PRODUCT_CATALOG,
        )
        with pytest.raises(RuntimeError) as failure:
            service.create_collaboration(make_create_command(), make_actor())
        assert failure.value is error
        # SQL and the MethodStore are not a shared transaction; never mask failure.
        assert len(repo.created_records) == 1

    def test_create_without_resource_refs_still_carries_empty_key(self):
        """空引用恒带空列表 key(spec resource-binding §2):新行与存量行
        (payload 无 key)判别有据。"""
        repo = _FakeContextCollabRepository()
        service = ContextCollabService(
            method_store=InMemoryMethodStore(),
            repository=repo,
            product_catalog=PRODUCT_CATALOG,
        )

        service.create_collaboration(
            make_create_command(resource_refs=[]), make_actor()
        )

        request = repo.create_calls[0]
        assert request.initial_event.payload["resource_refs"] == []

    def test_single_without_executor_rejected(self):
        service = ContextCollabService(
            method_store=InMemoryMethodStore(),
            repository=_FakeContextCollabRepository(),
            product_catalog=PRODUCT_CATALOG,
        )
        with pytest.raises(ContextCollabValidationError):
            service.create_collaboration(
                make_create_command(executor_ref=None), make_actor()
            )

    @pytest.mark.parametrize(
        "overrides",
        [
            {"method_kind": "swarm"},
            {"objective": ""},
        ],
        ids=[
            "unknown-method-kind",
            "empty-objective",
        ],
    )
    def test_invalid_create_payload_rejected(self, overrides):
        service = ContextCollabService(
            method_store=InMemoryMethodStore(),
            repository=_FakeContextCollabRepository(),
            product_catalog=PRODUCT_CATALOG,
        )
        with pytest.raises(ContextCollabValidationError):
            service.create_collaboration(make_create_command(**overrides), make_actor())

    @pytest.mark.parametrize("profile_ref", [None, VersionedProfileRef("external", 9)])
    def test_create_passes_product_metadata_without_resolving_profile(
        self, profile_ref
    ):
        repo = _FakeContextCollabRepository()
        store = InMemoryMethodStore()
        service = ContextCollabService(
            repository=repo,
            method_store=store,
            product_catalog=ProductCatalog(()),
        )
        created = service.create_collaboration(
            make_create_command(
                product_kind="external",
                workflow_kind="external_workflow",
                product_profile_ref=profile_ref,
            ),
            make_actor(),
        )

        request = repo.create_calls[0]
        assert request.product_kind == "external"
        assert request.workflow_kind == "external_workflow"
        assert request.product_profile_ref is profile_ref
        assert request.creation_facts["product_profile_ref"] == (
            {"profile_id": "external", "version": 9} if profile_ref else None
        )
        assert request.initial_event.payload == request.creation_facts
        [binding] = store.list_bindings(current_axes(), created.collet_id)
        assert binding.profile_versions == {"Single": "0.2"}
        assert binding.role_binding_refs == ()

    def test_service_satisfies_protocol(self):
        service = ContextCollabService(
            method_store=InMemoryMethodStore(),
            repository=_FakeContextCollabRepository(),
            product_catalog=PRODUCT_CATALOG,
        )
        assert isinstance(service, ContextCollabServiceProtocol)

    def test_create_projects_top_level_swarm_binding(self):
        repo = _FakeContextCollabRepository()
        store = InMemoryMethodStore()
        service = ContextCollabService(
            method_store=store,
            repository=repo,
            product_catalog=ProductCatalog(()),
        )
        created = service.create_collaboration(
            make_create_command(
                method_kind="Swarm", product_kind=None, workflow_kind=None
            ),
            make_actor(),
        )

        assert repo.create_calls[0].creation_facts["method_kind"] == "Swarm"
        [binding] = store.list_bindings(current_axes(), created.collet_id)
        assert binding.method_kind == "Swarm"
        assert binding.method_names == ("Swarm",)
        assert binding.profile_versions == {"Swarm": "0.2"}
        assert binding.state is BindingState.ACTIVE
        assert binding.parent_scope_ref is None
        assert store.list_composition_edges(current_axes(), created.collet_id) == []

    def test_create_rejects_bare_group_top_level(self):
        """裸 Group 仍仅允许嵌套绑定;Swarm 可直接作为顶层协作方法。"""
        service = ContextCollabService(
            method_store=InMemoryMethodStore(),
            repository=_FakeContextCollabRepository(),
            product_catalog=PRODUCT_CATALOG,
        )
        with pytest.raises(ContextCollabValidationError, match="top-level"):
            service.create_collaboration(
                make_create_command(method_kind="Group"), make_actor()
            )
        # 白名单内产品形态照常通过 G1 create 裁决(含 2026-11-13 新入的
        # 顶层单值 Open——任务代办产品的方法形态,spec 裁决 B)。
        service.create_collaboration(
            make_create_command(method_kind="Single"), make_actor()
        )
        service.create_collaboration(
            make_create_command(
                method_kind="Open + Group",
                product_kind="public_discussion",
                workflow_kind="forum_public_discussion",
            ),
            make_actor(),
        )
        # 裸 "Open" 顶层放行:任务代办产品的协作形态。
        service.create_collaboration(
            make_create_command(
                method_kind="Open",
                product_kind="task_commission",
                workflow_kind="task_commission_goal",
            ),
            make_actor(),
        )


class _FixedIdContextCollabRepository(_FakeContextCollabRepository):
    """创建恒返回固定 collab_context_id 的 fake;用于顶层投影幂等用例的
    同命令重放(真实仓储创建不设幂等键,重放语义由 store 幂等承载)。"""

    def create_collet(self, request: CreateColletRecordRequest) -> ContextCreatedRecord:
        self.create_calls.append(request)
        collab_context_id = "collet_projected_001"
        event_id = "evt_projected_001"
        task = Task(
            objective=request.objective,
            status=TaskStatus.CREATED.value,
            principal_ref=request.principal_ref,
            executor_refs=(
                (request.executor_ref,) if request.executor_ref is not None else ()
            ),
            task_id="task_" + collab_context_id,
            collet_ref=collab_context_id,
            version=1,
            parent_task_ref=None,
            completion_conditions=(),
            created_event_ref=event_id,
            last_event_ref=event_id,
        )
        collet = Collet(
            collet_id=collab_context_id,
            initiator_ref=request.initial_event.actor_ref,
            state=ColletState.ACTIVE,
            scope=request.space,
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
            creation_facts=request.creation_facts,
            task=task,
            initial_event=initial_event,
        )


class TestTopLevelProjection:
    """create_collet 成功路径的顶层 MethodBinding 投影(spec §9.2)。"""

    def test_create_projects_top_level_binding(self):
        store = InMemoryMethodStore()
        service = ContextCollabService(
            method_store=store,
            repository=_FixedIdContextCollabRepository(),
            product_catalog=PRODUCT_CATALOG,
        )
        created = service.create_collaboration(make_create_command(), make_actor())
        bindings = store.list_bindings(current_axes(), created.collet_id)
        assert len(bindings) == 1
        binding = bindings[0]
        assert binding.state is BindingState.ACTIVE
        assert binding.bind_reason == "create_collet"
        assert binding.method_kind == "Single"
        assert binding.method_names == ("Single",)
        assert binding.participant_ref == "bot_coding_001"

        # 同命令重复创建(同 ID):顶层投影幂等,store 仍只一行。
        again = service.create_collaboration(make_create_command(), make_actor())
        assert again.collet_id == created.collet_id
        assert len(store.list_bindings(current_axes(), created.collet_id)) == 1
        rebound = store.list_bindings(current_axes(), created.collet_id)[0]
        assert rebound.transition_reason == "top-level projection"

    def test_create_projects_open_group_method_names(self):
        store = InMemoryMethodStore()
        service = ContextCollabService(
            method_store=store,
            repository=_FixedIdContextCollabRepository(),
            product_catalog=PRODUCT_CATALOG,
        )
        created = service.create_collaboration(
            make_create_command(
                method_kind="Open + Group",
                executor_ref=None,
                product_kind="public_discussion",
                workflow_kind="forum_public_discussion",
            ),
            make_actor(),
        )
        binding = store.list_bindings(current_axes(), created.collet_id)[0]
        # 复合 wire 值投影为核心方法元组(spec §3.1),不能自由组合。
        assert binding.method_names == ("Open", "Group")
        assert binding.profile_versions == {"Open": "0.2", "Group": "0.2"}
        # 公开讨论无 executor:participant_ref 不虚造。
        assert binding.participant_ref is None


class _RecordingAuthorizer(StructuralContextCollabAuthorizer):
    """记录 check_create 调用以验证授权接缝可替换。"""

    def __init__(self) -> None:
        self.check_create_calls = []

    def check_create(self, *, method_kind, executor_ref, actor) -> None:
        self.check_create_calls.append((method_kind, executor_ref, actor))
        super().check_create(
            method_kind=method_kind, executor_ref=executor_ref, actor=actor
        )


class TestAuthorizerSeam:
    def test_default_authorizer_is_structural(self):
        service = ContextCollabService(
            method_store=InMemoryMethodStore(),
            repository=_FakeContextCollabRepository(),
            product_catalog=PRODUCT_CATALOG,
        )
        assert isinstance(service, ContextCollabServiceProtocol) and isinstance(
            service._authorizer, StructuralContextCollabAuthorizer
        )

    def test_injected_authorizer_checked_on_create(self):
        authorizer = _RecordingAuthorizer()
        service = ContextCollabService(
            method_store=InMemoryMethodStore(),
            repository=_FakeContextCollabRepository(),
            authorizer=authorizer,
            product_catalog=PRODUCT_CATALOG,
        )
        assert isinstance(authorizer, ContextCollabAuthorizerProtocol)
        actor = make_actor()
        service.create_collaboration(make_create_command(), actor)
        assert len(authorizer.check_create_calls) == 1
        method_kind, executor_ref, checked_actor = authorizer.check_create_calls[0]
        assert method_kind == "Single"
        assert executor_ref == EXECUTOR
        assert checked_actor is actor


def _fake_collet() -> Collet:
    return Collet(
        collet_id="collet_todo_001",
        initiator_ref=PRINCIPAL,
        state=ColletState.ACTIVE,
        scope=SpaceRef(space_id="space_workspace_001", environment="prod"),
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


def _fake_facts() -> dict:
    return creation_facts(
        method_kind="Single",
        executor_ref=AgentRef("bot", "bot_coding_001"),
        objective=OBJECTIVE,
        resource_refs=(),
        product_kind=None,
        workflow_kind=None,
    )


def _fake_task() -> Task:
    return Task(
        task_id="task_collet_todo_001",
        collet_ref="collet_todo_001",
        objective=OBJECTIVE,
        status=TaskStatus.CREATED.value,
        version=1,
        principal_ref=PRINCIPAL,
        executor_refs=((EXECUTOR,) if EXECUTOR is not None else ()),
        parent_task_ref=None,
        completion_conditions=(),
        created_event_ref="evt_001",
        last_event_ref="evt_001",
    )


def _append_command(
    *, actor: PrincipalRef, visibility: str = "private"
) -> EventAppendCommand:
    return EventAppendCommand(
        expected_version=1,
        event_kind="task.progress.updated",
        primitive_type="Activity",
        actor_ref=actor,
        parent_event=None,
        visibility=visibility,
        payload={},
    )


class TestStructuralAuthorizer:
    def test_check_create_single_requires_executor(self):
        with pytest.raises(ContextCollabValidationError):
            StructuralContextCollabAuthorizer().check_create(
                method_kind="Single", executor_ref=None, actor=make_actor()
            )

    def test_check_append_requires_principal_or_executor(self):
        authorizer = StructuralContextCollabAuthorizer()
        collet = _fake_collet()
        task = _fake_task()
        facts = _fake_facts()
        # principal 通过
        authorizer.check_append(
            collet,
            task,
            facts,
            None,
            _append_command(actor=PRINCIPAL),
            make_actor("u_1001"),
        )
        # executor(agent_id 即 bot_coding_001)通过
        bot_actor = PrincipalRef("bot", "bot_coding_001")
        authorizer.check_append(
            collet,
            task,
            facts,
            None,
            _append_command(actor=bot_actor),
            ActorContext(bot_actor, "a2a"),
        )
        # 陌生 actor 拒绝
        outsider = PrincipalRef("human", "u_9999")
        with pytest.raises(ContextCollabValidationError):
            authorizer.check_append(
                collet,
                task,
                facts,
                None,
                _append_command(actor=outsider),
                ActorContext(outsider, "api"),
            )

    def test_filter_visible_events_by_actor_role(self):
        authorizer = StructuralContextCollabAuthorizer()
        collet = _fake_collet()
        task = _fake_task()
        facts = _fake_facts()
        events = [
            _event("evt_pub", "public"),
            _event("evt_ws", "workspace"),
            _event("evt_priv", "private"),
        ]

        principal_view = authorizer.filter_visible_events(
            collet, task, facts, None, events, make_actor("u_1001")
        )
        assert [e.event_id for e in principal_view] == ["evt_pub", "evt_ws", "evt_priv"]

        executor_view = authorizer.filter_visible_events(
            collet,
            task,
            facts,
            None,
            events,
            ActorContext(PrincipalRef("bot", "bot_coding_001"), "a2a"),
        )
        assert [e.event_id for e in executor_view] == ["evt_pub", "evt_ws"]

        outsider = PrincipalRef("human", "u_9999")
        outsider_view = authorizer.filter_visible_events(
            collet, task, facts, None, events, ActorContext(outsider, "api")
        )
        assert [e.event_id for e in outsider_view] == ["evt_pub"]
