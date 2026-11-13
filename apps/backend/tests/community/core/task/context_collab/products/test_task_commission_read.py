"""任务代办产品层读用例单测(plan T3;spec §5.2 读投影)。

覆盖:
- 列表:按 product_kind="task_commission" 过滤、gmt_create 倒序、
  message_count(含首帖正文);
- 详情:title 读源=初始事件 payload(回退伴随 spec)、body=首条 public
  Message、**status 为机器权威**(快照直读)、事件流公开投影含 Task
  控制事件(推进历史是代办消费面的核心);
- 外 profile 404、未知 collet 404。
"""

import pytest
from datetime import datetime

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
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
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ColletState,
)
from agentclaw.community.core.task.context_collab.l6_products.context_spec import (
    ContextSpec,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import Task
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service import (
    TaskCommissionService,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    PublishTaskCommand,
    TaskAdvanceCommand,
)
from tests.community.core.task.context_collab.product_catalog import PRODUCT_CATALOG

ASSIGNEE = "bot_commission_001"
PRINCIPAL = PrincipalRef(principal_type="human", principal_id="u_1001")
SPACE = SpaceRef(space_id="space_workspace_001", environment="prod")


def _actor() -> ActorContext:
    return ActorContext(actor=PRINCIPAL, source="api")


def _bot_actor(agent_id: str = ASSIGNEE) -> ActorContext:
    return ActorContext(actor=PrincipalRef("bot", agent_id), source="api")


class _FakeRepo:
    """受控仓储:产品全流程可用(发布/推进/读三面)。"""

    def __init__(self) -> None:
        self._status: dict[str, ContextStatusRecord] = {}
        self._gmt_create: dict[str, datetime] = {}

    def get_current_status(self, collab_context_id, *, trunk_events=False):
        return self._status.get(collab_context_id)

    def list_collets_by_product(self, product_kind: str):
        rows = [
            (
                record.collet,
                record.context_spec,
                self._gmt_create.get(
                    record.collet.collet_id, datetime(2026, 11, 13, 9, 0, 0)
                ),
            )
            for record in self._status.values()
            if record.context_spec.product_kind == product_kind
        ]
        return sorted(rows, key=lambda row: row[2], reverse=True)

    def count_message_events(self, collab_context_ids):
        return {
            cid: sum(
                1
                for event in self._status[cid].events
                if event.primitive_type == "Message"
            )
            for cid in collab_context_ids
            if cid in self._status
        }

    def create_context(self, request):
        raise NotImplementedError("read tests use full-flow service")


class TestReadCases:
    """经真服务全流程(发布+推进)生成数据后再读——读投影吃的是流程真值。"""

    def _service(self):
        from tests.community.core.task.context_collab.products.test_task_commission_publish import (
            _method_port,
        )

        write_fake = _WriteRepoShim()
        store = InMemoryMethodStore()
        service = TaskCommissionService(
            collab_repo=write_fake,
            authorizer=StructuralContextCollabAuthorizer(),
            method_store=store,
            method_ops_port=_method_port(write_fake, store),
            product_catalog=PRODUCT_CATALOG,
        )
        return service, write_fake

    def test_full_flow_read_back(self):
        service, repo = self._service()
        created = service.publish_task(
            PublishTaskCommand(
                title="整理代办事项目录",
                body="任务描述正文。",
                space=SPACE,
                assignee_agent_id=ASSIGNEE,
            ),
            _actor(),
        )
        collet_id = created.collet_id
        repo._gmt_create[collet_id] = datetime(2026, 11, 13, 8, 0, 0)
        # bot 开工+提交,发起人验收——读面应见机器状态与控制历史。
        service.start_task(_adv(collet_id), _bot_actor())
        service.submit_task(_adv(collet_id, artifacts=["doc://r1"]), _bot_actor())
        service.accept_task(_adv(collet_id), _actor())

        detail = service.detail_task(collet_id)
        assert detail.collet_id == collet_id
        assert detail.title == "整理代办事项目录"
        assert detail.status == "done"
        assert detail.body is not None
        assert detail.body.content == "任务描述正文。"
        kinds = [e["event_kind"] for e in detail.events]
        assert kinds == [
            "task.created",
            "user_interaction.posted",
            "task.started",
            "task.submitted",
            "task.accepted",
        ]
        started = next(e for e in detail.events if e["event_kind"] == "task.started")
        assert started["actor_id"] == "bot_commission_001"

        listed = service.list_tasks()
        assert [v.collet_id for v in listed] == [collet_id]
        summary = listed[0]
        assert summary.title == "整理代办事项目录"
        assert summary.initiator_id == "u_1001"
        assert summary.message_count == 1

    def test_list_orders_desc_and_filters_profile(self):
        service, repo = self._service()
        older = service.publish_task(
            PublishTaskCommand(
                title="早任务", body="x", space=SPACE, assignee_agent_id=ASSIGNEE
            ),
            _actor(),
        )
        repo._gmt_create[older.collet_id] = datetime(2026, 11, 13, 7, 0, 0)
        newer = service.publish_task(
            PublishTaskCommand(
                title="晚任务", body="y", space=SPACE, assignee_agent_id=ASSIGNEE
            ),
            _actor(),
        )
        repo._gmt_create[newer.collet_id] = datetime(2026, 11, 13, 9, 0, 0)
        # 外 profile 一行:不得入列表。

        repo._status["collet_foreign_row_1"] = _status_record(
            "collet_foreign_row_1", _open_group_spec("公开讨论帖")
        )

        views = service.list_tasks()

        assert [v.title for v in views] == ["晚任务", "早任务"]

    def test_detail_rejects_foreign_profile_and_unknown(self):
        service, repo = self._service()
        repo._status["collet_foreign_row_2"] = _status_record(
            "collet_foreign_row_2", _open_group_spec("又一个公开讨论帖")
        )

        with pytest.raises(ContextCollabNotFoundError):
            service.detail_task("collet_foreign_row_2")
        with pytest.raises(ContextCollabNotFoundError):
            service.detail_task("collet_missing_404")


def _adv(collab_context_id, **overrides):
    fields = {"collab_context_id": collab_context_id, "note": None, "artifacts": []}
    fields.update(overrides)
    return TaskAdvanceCommand(**fields)


def _open_group_spec(objective: str) -> ContextSpec:
    return ContextSpec(
        space=SPACE,
        principal_ref=PRINCIPAL,
        method_kind="Open + Group",
        executor_ref=None,
        objective=objective,
        resource_refs=(),
        product_kind="bbs_public_discussion",
        workflow_kind="bbs_public_discussion",
    )


def _status_record(collab_context_id: str, spec: ContextSpec):
    return ContextStatusRecord(
        collet=Collet(
            collet_id=collab_context_id,
            initiator_ref=PRINCIPAL,
            state=ColletState.ACTIVE,
            scope=SPACE,
            current_event_id=None,
            version=1,
        ),
        context_spec=spec,
        tasks=(
            Task(
                objective=spec.objective,
                status="created",
                principal_ref=PRINCIPAL,
                executor_ref=None,
                task_id="task_" + "collet_other",
                collet_ref="collet_other",
                version=1,
                parent_task_ref=None,
                completion_conditions=(),
                created_event_ref="evt_fixture",
                last_event_ref="evt_fixture",
            ),
        )
        if Task(
            objective=spec.objective,
            status="created",
            principal_ref=PRINCIPAL,
            executor_ref=None,
            task_id="task_" + "collet_other",
            collet_ref="collet_other",
            version=1,
            parent_task_ref=None,
            completion_conditions=(),
            created_event_ref="evt_fixture",
            last_event_ref="evt_fixture",
        )
        is not None
        else (),
        task=Task(
            objective=spec.objective,
            status="created",
            principal_ref=PRINCIPAL,
            executor_ref=None,
            task_id="task_" + "collet_other",
            collet_ref="collet_other",
            version=1,
            parent_task_ref=None,
            completion_conditions=(),
            created_event_ref="evt_fixture",
            last_event_ref="evt_fixture",
        ),
        events=[],
    )


class _WriteRepoShim(_FakeRepo):
    """在只读 fake 上补齐写三方法(委托真流程测试的所有编排步骤)。"""

    def __init__(self):
        super().__init__()

    def create_context(self, request):
        from uuid import uuid4

        collab_context_id = "collet_" + uuid4().hex
        event_id = "evt_" + uuid4().hex
        from datetime import timezone as _tz

        now = datetime.now(_tz.utc)
        task = Task(
            objective=request.context_spec.objective,
            status="created",
            principal_ref=request.context_spec.principal_ref,
            executor_ref=request.context_spec.executor_ref,
            task_id="task_" + collab_context_id,
            collet_ref=collab_context_id,
            version=1,
            parent_task_ref=None,
            completion_conditions=(),
            created_event_ref=event_id,
            last_event_ref=event_id,
        )
        from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
            Collet as _Collet,
            ColletState as _ColletState,
        )
        from agentclaw.community.core.task.context_collab.persistence.repository import (
            ContextCreatedRecord,
        )

        collet = _Collet(
            collet_id=collab_context_id,
            initiator_ref=request.initial_event.actor_ref,
            state=_ColletState.ACTIVE,
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
            occurred_at=now,
        )
        self._status[collab_context_id] = ContextStatusRecord(
            collet=collet,
            context_spec=request.context_spec,
            tasks=(task,) if task is not None else (),
            task=task,
            events=[initial_event],
        )
        return ContextCreatedRecord(
            collet=collet,
            context_spec=request.context_spec,
            task=task,
            initial_event=initial_event,
        )

    def append_event(self, collab_context_id, request):
        import dataclasses
        from uuid import uuid4
        from datetime import datetime as _dt, timezone as _tz

        record = self._status[collab_context_id]
        event_id = "evt_" + uuid4().hex
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
            occurred_at=_dt.now(_tz.utc),
        )
        new_collet = dataclasses.replace(
            record.collet,
            version=record.collet.version + 1,
            current_event_id=event_id,
        )
        self._status[collab_context_id] = ContextStatusRecord(
            collet=new_collet,
            context_spec=record.context_spec,
            tasks=(new_task,) if new_task is not None else (),
            task=new_task,
            events=[*record.events, event],
        )
        from agentclaw.community.core.task.context_collab.persistence.repository import (
            EventAppendedRecord,
        )

        return EventAppendedRecord(
            event=event,
            collet=new_collet,
            context_spec=record.context_spec,
            task=new_task,
        )
