"""任务代办产品层发布用例单测(plan T2;spec §5.1/§5.2)。

覆盖:
- 发布编织:L6/L5/L4 取值——method_kind=Open(顶层单值)、
  executor_ref=AgentRef("bot", assignee)、workflow_kind=
  task_commission_goal、六键 creation_payload、首帖正文 Message、
  L5 顶层单值 MethodBinding(无 roster);
- 视图五字段(status 恒 "created");
- 空 assignee/空标题/空正文拒绝。

fake 沿公开讨论 write 测试先例(in-memory 仓储 + 真 store)。
"""

import dataclasses
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    StructuralContextCollabAuthorizer,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.legacy_collet import (
    LegacyCollet,
    ColletState,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import Task
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    AppendEventRecordRequest,
    ContextCreatedRecord,
    ContextStatusRecord,
    CreateColletRecordRequest,
    EventAppendedRecord,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_execution_contracts import (
    SwarmExecutionControl,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service import (
    TaskCommissionService,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    PublishTaskCommand,
)

from tests.community.core.task.context_collab.product_catalog import PRODUCT_CATALOG

ASSIGNEE = "bot_commission_001"
PRINCIPAL = PrincipalRef(principal_type="human", principal_id="u_1001")
SPACE = SpaceRef(space_id="space_workspace_001", environment="prod")
TITLE = "整理任务代办 spec 并提交评审"
BODY = "任务描述:按 §5 逐项核对编排管。"


def _actor(principal_id: str = "u_1001") -> ActorContext:
    return ActorContext(actor=PrincipalRef("human", principal_id), source="api")


def _bot_actor(agent_id: str = ASSIGNEE) -> ActorContext:
    return ActorContext(actor=PrincipalRef("bot", agent_id), source="api")


class _FakeRepo:
    """受控 in-memory 仓储:协作编排三方法可用,列表/计数不进本测试面。"""

    def __init__(self) -> None:
        self._status: dict[str, ContextStatusRecord] = {}
        self.create_calls: list[CreateColletRecordRequest] = []
        self.append_calls: list[tuple[str, AppendEventRecordRequest]] = []

    def create_collet(self, request: CreateColletRecordRequest):
        self.create_calls.append(request)
        collab_context_id = "collet_" + uuid4().hex
        event_id = "evt_" + uuid4().hex
        now = datetime.now(timezone.utc)
        task = Task(
            objective=request.objective,
            status="created",
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
        collet = LegacyCollet(
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
            occurred_at=now,
        )
        self._status[collab_context_id] = ContextStatusRecord(
            collet=collet,
            creation_facts=request.creation_facts,
            tasks=(task,) if task is not None else (),
            task=task,
            events=[initial_event],
        )
        return ContextCreatedRecord(
            collet=collet,
            creation_facts=request.creation_facts,
            task=task,
            initial_event=initial_event,
        )

    def append_event(self, collab_context_id, request: AppendEventRecordRequest):
        record = self._status[collab_context_id]
        event_id = "evt_" + uuid4().hex
        # 快照推进执行 snapshot_updater:与产品状态裁决一致(非恒等)。
        new_task = (
            request.task_update.apply(
                record.task,
                event_id=event_id,
                event_kind=request.event_kind,
                payload=request.payload,
            )
            if request.task_update is not None
            else record.task
        )
        event = EventRecord(
            collab_context_id=collab_context_id,
            event_id=event_id,
            parent_event=request.parent_event,
            event_kind=request.event_kind,
            primitive_type=request.primitive_type,
            actor_ref=request.actor_ref,
            visibility=request.visibility,
            payload=dict(request.payload),
            context_version_after=record.collet.version + 1,
            occurred_at=datetime.now(timezone.utc),
        )
        self.append_calls.append((collab_context_id, request))
        new_collet = dataclasses.replace(
            record.collet,
            version=record.collet.version + 1,
        )
        self._status[collab_context_id] = ContextStatusRecord(
            collet=new_collet,
            creation_facts=record.creation_facts,
            tasks=(new_task,) if new_task is not None else (),
            task=new_task,
            events=[*record.events, event],
        )
        return EventAppendedRecord(
            event=event,
            collet=new_collet,
            creation_facts=record.creation_facts,
            task=new_task,
        )

    def get_current_status(self, collab_context_id, *, trunk_events=False):
        return self._status.get(collab_context_id)

    def list_collets_by_product(self, product_kind: str):
        raise NotImplementedError("fake does not implement product listing")

    def count_message_events(self, collab_context_ids):
        raise NotImplementedError("fake does not implement message counting")


def _publish_command(title: str = TITLE, body: str = BODY):
    return PublishTaskCommand(
        title=title, body=body, space=SPACE, assignee_agent_id=ASSIGNEE
    )


def _method_port(repo, store):
    """真方法编排缝:真适配器 + 真 OpenMethodService(发布的 E2E 语义
    是裁决内容——方法生命周期必须在每个产品的发布测试里真实走过)。"""
    from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
        StructuralMethodOpsAuthorizer,
    )
    from agentclaw.community.core.task.context_collab.l5_methods.operations.open.service import (
        OpenMethodService,
    )
    from agentclaw.community.di.modules.task.task_commission_self_application import (
        TaskCommissionSelfApplicationAdapter,
    )

    return TaskCommissionSelfApplicationAdapter(
        open_ops=OpenMethodService(
            store=store,
            authorizer=StructuralMethodOpsAuthorizer(),
            collab_repo=repo,
        ),
        product_catalog=PRODUCT_CATALOG,
        store=store,
        repository=repo,
        group_ops=Mock(),
    )


def _service(repo: _FakeRepo | None = None, store: InMemoryMethodStore | None = None):
    repo = repo or _FakeRepo()
    store = store or InMemoryMethodStore()
    service = TaskCommissionService(
        collab_repo=repo,
        authorizer=StructuralContextCollabAuthorizer(),
        method_store=store,
        method_ops_port=_method_port(repo, store),
        swarm_execution=Mock(spec=SwarmExecutionControl),
        product_catalog=PRODUCT_CATALOG,
    )
    return service, repo, store


def _top_binding(store, repo, collab_context_id):
    from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
        _now,
        current_axes,
    )
    from agentclaw.community.core.task.context_collab.services.method_lifecycle_service import (
        ensure_top_level_binding,
    )

    return ensure_top_level_binding(
        store,
        current_axes(),
        collab_context_id,
        repo,
        _actor(),
        now=_now(),
        catalog=PRODUCT_CATALOG,
    )


class TestPublishTask:
    def test_publish_wires_open_commission(self):
        service, repo, store = _service()

        view = service.publish_task(_publish_command(), _actor())

        request = repo.create_calls[0]
        spec = request.creation_facts
        assert spec["objective"] == TITLE
        assert spec["method_kind"] == "Open"
        assert spec["executor_ref"] is not None
        assert spec["executor_ref"]["agent_id"] == ASSIGNEE
        assert spec["product_kind"] == "task_commission"
        assert spec["workflow_kind"] is None
        assert request.initial_event.payload == spec

        collet_id, append_request = repo.append_calls[0]
        assert collet_id == view.collet_id
        assert append_request.expected_version == 1
        assert append_request.event_kind == "user_interaction.posted"
        assert append_request.primitive_type == "Message"
        assert append_request.visibility == "public"
        assert append_request.payload == {"content": BODY}

        binding = _top_binding(store, repo, view.collet_id)
        assert binding.method_kind == "Open"
        assert binding.state is BindingState.ACTIVE

        assert view.title == TITLE
        assert view.status == "created"
        assert view.context_version == 2

    def test_publish_rejects_blank_fields(self):
        service, repo, _ = _service()
        with pytest.raises(ContextCollabValidationError):
            service.publish_task(
                PublishTaskCommand(
                    title=" ",
                    body=BODY,
                    space=SPACE,
                    assignee_agent_id=ASSIGNEE,
                ),
                _actor(),
            )
        with pytest.raises(ContextCollabValidationError):
            service.publish_task(
                PublishTaskCommand(
                    title=TITLE,
                    body="",
                    space=SPACE,
                    assignee_agent_id=ASSIGNEE,
                ),
                _actor(),
            )
        with pytest.raises(ContextCollabValidationError):
            service.publish_task(
                PublishTaskCommand(
                    title=TITLE,
                    body=BODY,
                    space=SPACE,
                    assignee_agent_id="  ",
                ),
                _actor(),
            )
        assert repo.create_calls == []
        assert repo.append_calls == []
