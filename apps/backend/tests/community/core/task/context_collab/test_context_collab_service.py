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
from agentclaw.community.plugins.local.context_collab_runtime import (
    LocalContextCollabRuntimePlugin,
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

    def __init__(self) -> None:
        self.create_calls: list[CreateContextRecordRequest] = []

    def create_context(
        self, request: CreateContextRecordRequest
    ) -> ContextCreatedRecord:
        self.create_calls.append(request)

        collab_context_id = "collet_" + uuid4().hex
        event_id = "evt_" + uuid4().hex
        collet = Collet(
            collab_context_id=collab_context_id,
            actor_ref=None,
            context_spec=request.context_spec,
            current_snapshot=CurrentSnapshot(
                objective=request.context_spec.objective,
                status=TaskStatus.CREATED.value,
                principal_ref=request.context_spec.principal_ref,
                executor_ref=request.context_spec.executor_ref,
                method_kind=request.context_spec.method_kind,
                progress=ProgressSnapshot(percent=0, current_event_id=None),
            ),
            current_event_id=event_id,
            context_version=1,
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
        return ContextCreatedRecord(collet=collet, initial_event=initial_event)

    def append_event(self, collab_context_id, request):
        raise NotImplementedError("fake only implements create_context")

    def get_current_status(self, collab_context_id, *, trunk_events):
        raise NotImplementedError("fake only implements create_context")


class TestCreateContext:
    def test_creates_collet_with_initial_event(self):
        repo = _FakeContextCollabRepository()
        assert isinstance(repo, ContextCollabRepositoryProtocol)
        service = ContextCollabService(
            repository=repo, runtime=LocalContextCollabRuntimePlugin()
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
        assert request.initial_event.payload == {"objective": OBJECTIVE}

    def test_single_without_executor_rejected(self):
        service = ContextCollabService(
            repository=_FakeContextCollabRepository(),
            runtime=LocalContextCollabRuntimePlugin(),
        )
        with pytest.raises(ContextCollabValidationError):
            service.create_context(make_create_command(executor_ref=None), make_actor())

    @pytest.mark.parametrize(
        "overrides",
        [
            {"product_kind": "os"},
            {"workflow_kind": "unknown"},
            {"method_kind": "Swarm"},
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
            repository=_FakeContextCollabRepository(),
            runtime=LocalContextCollabRuntimePlugin(),
        )
        with pytest.raises(ContextCollabValidationError):
            service.create_context(make_create_command(**overrides), make_actor())

    def test_service_satisfies_protocol(self):
        service = ContextCollabService(
            repository=_FakeContextCollabRepository(),
            runtime=LocalContextCollabRuntimePlugin(),
        )
        assert isinstance(service, ContextCollabServiceProtocol)


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
            repository=_FakeContextCollabRepository(),
            runtime=LocalContextCollabRuntimePlugin(),
        )
        assert isinstance(service, ContextCollabServiceProtocol) and isinstance(
            service._authorizer, StructuralContextCollabAuthorizer
        )

    def test_injected_authorizer_checked_on_create(self):
        authorizer = _RecordingAuthorizer()
        service = ContextCollabService(
            repository=_FakeContextCollabRepository(),
            runtime=LocalContextCollabRuntimePlugin(),
            authorizer=authorizer,
        )
        assert isinstance(authorizer, ContextCollabAuthorizerProtocol)
        actor = make_actor()
        service.create_context(make_create_command(), actor)
        assert len(authorizer.check_create_calls) == 1
        spec, checked_actor = authorizer.check_create_calls[0]
        assert spec.method_kind == "Single"
        assert checked_actor is actor


def _fake_collet(
    *, principal_id: str = "u_1001", executor_id: str | None = "bot_coding_001"
) -> Collet:
    """构造带 principal/executor 的最小 Collet,供授权器身份测试用。"""
    return Collet(
        collab_context_id="collet_todo_001",
        actor_ref=None,
        context_spec=ContextSpec(
            space=SpaceRef(space_id="space_workspace_001", environment="prod"),
            principal_ref=PrincipalRef("human", principal_id),
            method_kind="Single",
            executor_ref=(
                AgentRef("bot", executor_id) if executor_id is not None else None
            ),
            objective=OBJECTIVE,
            resource_refs=(),
            product_kind=None,
            workflow_kind=None,
        ),
        current_snapshot=CurrentSnapshot(
            objective=OBJECTIVE,
            status=TaskStatus.CREATED.value,
            principal_ref=PrincipalRef("human", principal_id),
            executor_ref=(
                AgentRef("bot", executor_id) if executor_id is not None else None
            ),
            method_kind="Single",
            progress=ProgressSnapshot(),
        ),
        current_event_id=None,
        context_version=1,
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
            StructuralContextCollabAuthorizer().check_create(spec, make_actor())

    def test_check_append_requires_principal_or_executor(self):
        authorizer = StructuralContextCollabAuthorizer()
        collet = _fake_collet()
        # principal 通过
        authorizer.check_append(collet, None, make_actor("u_1001"))
        # executor(agent_id 即 bot_coding_001)通过
        authorizer.check_append(collet, None, make_actor("bot_coding_001"))
        # 陌生 actor 拒绝
        with pytest.raises(ContextCollabValidationError):
            authorizer.check_append(collet, None, make_actor("u_9999"))

    def test_filter_visible_events_by_actor_role(self):
        authorizer = StructuralContextCollabAuthorizer()
        collet = _fake_collet()
        events = [
            _event("evt_pub", "public"),
            _event("evt_ws", "workspace"),
            _event("evt_priv", "private"),
        ]

        principal_view = authorizer.filter_visible_events(
            collet, events, make_actor("u_1001")
        )
        assert [e.event_id for e in principal_view] == [
            "evt_pub",
            "evt_ws",
            "evt_priv",
        ]

        executor_view = authorizer.filter_visible_events(
            collet, events, make_actor("bot_coding_001")
        )
        assert [e.event_id for e in executor_view] == ["evt_pub", "evt_ws"]

        outsider_view = authorizer.filter_visible_events(
            collet, events, make_actor("u_9999")
        )
        assert [e.event_id for e in outsider_view] == ["evt_pub"]
