"""Context collab 服务追加事件用例单测(Task 10)。

覆盖 ``ContextCollabService.append_event``(spec §6.3 指定执行请求
示例):G4 事件体注册表(service 层二道闸)、G1 结构授权、
``team_todo_execution`` 状态机迁移与守卫(含 Controller Ruling R4
的 bot-actor 值比较)、仓储把守的版本/parent 校验错误原样上抛,
以及幂等重放仍返回 ``accepted=True``。仓储用手写 in-memory fake
承载真实语义(重放先于版本检查、``snapshot_updater`` 求值后 CAS 推进
版本),不触碰数据库。
"""

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabParentEventError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g2_keep_reliable_live.reliability import (
    ensure_expected_version,
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
    AppendEventRecordRequest,
    ContextCollabRepositoryProtocol,
    ContextCreatedRecord,
    ContextStatusRecord,
    CreateContextRecordRequest,
    EventAppendedRecord,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
    ContextCollabServiceProtocol,
    ContextCreateCommand,
    EventAcceptedResult,
    EventAppendCommand,
)
from agentclaw.community.core.task.context_collab.services.context_collab_service import (
    ContextCollabService,
)

OBJECTIVE = "整理 Context 协作协议评审结论并提交可评审 PR"
PRINCIPAL = PrincipalRef(principal_type="human", principal_id="u_1001")
EXECUTOR = AgentRef(agent_type="bot", agent_id="bot_coding_001")


def make_create_command(**overrides) -> ContextCreateCommand:
    """构造创建命令(spec §6.2 示例);与 Task 7 测试同形态,可按字段覆盖。"""
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


def make_actor(
    principal_type: str = "human", principal_id: str = "u_1001"
) -> ActorContext:
    return ActorContext(
        actor=PrincipalRef(principal_type, principal_id), source="api"
    )


def make_append_command(event_kind: str, **overrides) -> EventAppendCommand:
    """构造追加事件命令(spec §6.3 示例形);默认可追加的合法事件体。"""
    fields = {
        "expected_version": 1,
        "event_kind": event_kind,
        "primitive_type": "Message",
        "actor_ref": PRINCIPAL,
        "parent_event": None,
        "visibility": "workspace",
        "payload": {"content": "上下文做一下补充"},
    }
    fields.update(overrides)
    return EventAppendCommand(**fields)


class _FakeContextCollabRepository:
    """手写 in-memory 仓储 fake,承载三个用例的仓储真实语义。

    创建(不设幂等键,重复调用即重复创建):生成 ``collet_`` / ``evt_`` ID、
    初始快照(status=created、progress 全 0)、初始 task.created 事件。
    追加:NotFound → 幂等重放(先于版本检查,
    返回原事件 + 当前行 collet)→ ``ensure_expected_version`` →
    parent 存在性检查 → ``snapshot_updater`` 求值 → CAS 推进版本。
    """

    def __init__(self) -> None:
        self.collets: dict[str, Collet] = {}
        self.events: dict[str, list[EventRecord]] = {}
        self.append_calls: list[tuple[str, AppendEventRecordRequest]] = []

    def create_context(self, request: CreateContextRecordRequest) -> ContextCreatedRecord:
        collab_context_id = "collet_" + uuid4().hex
        event_id = "evt_" + uuid4().hex
        collet = Collet(
            collab_context_id=collab_context_id,
            context_spec=request.context_spec,
            current_snapshot=CurrentSnapshot(
                objective=request.context_spec.objective,
                status=TaskStatus.CREATED.value,
                principal_ref=request.context_spec.principal_ref,
                executor_ref=request.context_spec.executor_ref,
                method_kind=request.context_spec.method_kind,
                progress=ProgressSnapshot(),
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
        self.collets[collab_context_id] = collet
        self.events[collab_context_id] = [initial_event]
        return ContextCreatedRecord(collet=collet, initial_event=initial_event)

    def get_current_status(
        self, collab_context_id: str
    ) -> "ContextStatusRecord | None":
        collet = self.collets.get(collab_context_id)
        if collet is None:
            return None
        events = list(self.events.get(collab_context_id, []))
        return ContextStatusRecord(collet=collet, events=events)

    def append_event(
        self, collab_context_id: str, request: "AppendEventRecordRequest"
    ) -> "EventAppendedRecord":
        self.append_calls.append((collab_context_id, request))
        collet = self.collets.get(collab_context_id)
        if collet is None:
            raise ContextCollabNotFoundError(
                f"collaboration context {collab_context_id!r} not found"
            )
        events = self.events.setdefault(collab_context_id, [])
        ensure_expected_version(request.expected_version, collet.context_version)
        if request.parent_event is not None and request.parent_event not in {
            event.event_id for event in events
        }:
            raise ContextCollabParentEventError(
                f"parent_event {request.parent_event!r} does not exist in "
                f"collaboration context {collab_context_id!r}"
            )
        context_version = request.expected_version + 1
        event = EventRecord(
            collab_context_id=collab_context_id,
            event_id="evt_" + uuid4().hex,
            parent_event=request.parent_event,
            event_kind=request.event_kind,
            primitive_type=request.primitive_type,
            actor_ref=request.actor_ref,
            visibility=request.visibility,
            payload=dict(request.payload),
            context_version_after=context_version,
            occurred_at=datetime.now(timezone.utc),
        )
        # snapshot_updater 在"行锁"内对当前快照求值后 CAS 推进版本。
        new_snapshot = request.snapshot_updater(collet.current_snapshot)
        updated = replace(
            collet,
            current_snapshot=new_snapshot,
            current_event_id=event.event_id,
            context_version=context_version,
        )
        events.append(event)
        self.collets[collab_context_id] = updated
        return EventAppendedRecord(event=event, collet=updated)


def _make_service(**command_overrides):
    repo = _FakeContextCollabRepository()
    assert isinstance(repo, ContextCollabRepositoryProtocol)
    service = ContextCollabService(repository=repo)
    assert isinstance(service, ContextCollabServiceProtocol)
    created = service.create_context(
        make_create_command(**command_overrides), make_actor()
    )
    return repo, service, created


class TestAppendEventHappyPath:
    def test_user_interaction_message_accepted(self):
        """spec §6.3 指定执行请求示例:无规则事件不动 status,版本 +1。"""
        repo, service, created = _make_service()
        initial_event_id = created.current_event["event_id"]
        command = make_append_command(
            "user_interaction.posted",
            expected_version=1,
            payload={
                "message_kind": "user_interaction",
                "content": "上下文做一下补充xxx",
            },
            parent_event=initial_event_id,
            visibility="workspace",
        )

        result = service.append_event(
            created.collab_context_id, command, make_actor()
        )

        assert isinstance(result, EventAcceptedResult)
        assert result.accepted is True
        assert result.collab_context_id == created.collab_context_id
        assert result.event_id.startswith("evt_")
        assert result.parent_event == initial_event_id
        assert result.event_kind == "user_interaction.posted"
        assert result.primitive_type == "Message"
        assert result.context_version == 2
        assert result.current_status == "created"

        # 服务传给仓储的请求语义(parent 透传,snapshot_updater 是纯函数)。
        request = repo.append_calls[0][1]
        assert isinstance(request, AppendEventRecordRequest)
        assert repo.append_calls[0][0] == created.collab_context_id
        assert request.expected_version == 1
        assert request.event_kind == "user_interaction.posted"
        assert request.parent_event == initial_event_id
        assert request.actor_ref == PRINCIPAL
        assert callable(request.snapshot_updater)

        # 仓储真的执行了 snapshot_updater 并 CAS 推进版本。
        collet = repo.collets[created.collab_context_id]
        assert collet.context_version == 2
        assert collet.current_snapshot.status == "created"
        assert collet.current_event_id == result.event_id
        assert len(repo.events[created.collab_context_id]) == 2


class TestStateMachineTransitions:
    """``team_todo_execution`` 机器驱动迁移与守卫(G4 §4.1 / Ruling R4)。"""

    def test_task_started_by_principal_moves_to_in_progress(self):
        repo, service, created = _make_service()
        command = make_append_command(
            "task.started",
            primitive_type="Task",
            payload={},
            visibility="public",
        )
        result = service.append_event(
            created.collab_context_id, command, make_actor()
        )
        assert result.accepted is True
        assert result.event_kind == "task.started"
        assert result.context_version == 2
        assert result.current_status == "in_progress"

    def test_task_started_by_executor_bot_passes_guard_r4(self):
        """R4:bot 型 actor 与 executor 按值比较(agent_id)即视为执行者。"""
        repo, service, created = _make_service()
        bot_actor = make_actor("bot", "bot_coding_001")
        command = make_append_command(
            "task.started",
            primitive_type="Task",
            payload={},
            actor_ref=bot_actor.actor,
            visibility="public",
        )
        result = service.append_event(created.collab_context_id, command, bot_actor)
        assert result.accepted is True
        assert result.context_version == 2
        assert result.current_status == "in_progress"

    def test_task_submitted_without_artifacts_rejected(self):
        repo, service, created = _make_service()
        cid = created.collab_context_id
        service.append_event(
            cid,
            make_append_command(
                "task.started",
                primitive_type="Task", payload={}, visibility="public",
            ),
            make_actor(),
        )
        submit = make_append_command(
            "task.submitted",
            expected_version=2,
            primitive_type="Task",
            payload={"artifacts": []},
            visibility="public",
        )
        with pytest.raises(ContextCollabValidationError, match="required_artifacts"):
            service.append_event(cid, submit, make_actor())
        # 被拒事件不落库,状态与版本不动。
        collet = repo.collets[cid]
        assert collet.context_version == 2
        assert collet.current_snapshot.status == "in_progress"
        assert len(repo.events[cid]) == 2

    def test_task_accepted_by_non_principal_rejected(self):
        repo, service, created = _make_service()
        cid = created.collab_context_id
        principal = make_actor()
        service.append_event(
            cid,
            make_append_command(
                "task.started",
                primitive_type="Task", payload={}, visibility="public",
            ),
            principal,
        )
        service.append_event(
            cid,
            make_append_command(
                "task.submitted",
                expected_version=2, primitive_type="Task",
                payload={"artifacts": ["art_pr_001"]}, visibility="public",
            ),
            principal,
        )
        # executor 可以通过 G1 结构授权,但守卫要求 principal 复核。
        executor_actor = make_actor("human", "bot_coding_001")
        accept = make_append_command(
            "task.accepted",
            expected_version=3,
            primitive_type="Task",
            actor_ref=executor_actor.actor,
            payload={},
            visibility="public",
        )
        with pytest.raises(
            ContextCollabValidationError, match="actor_is_responsible_principal"
        ):
            service.append_event(cid, accept, executor_actor)
        collet = repo.collets[cid]
        assert collet.current_snapshot.status == "reviewing"
        assert collet.context_version == 3

    def test_task_lifecycle_reaches_done(self):
        repo, service, created = _make_service()
        cid = created.collab_context_id
        principal = make_actor()

        started = service.append_event(
            cid,
            make_append_command(
                "task.started",
                primitive_type="Task", payload={}, visibility="public",
            ),
            principal,
        )
        submitted = service.append_event(
            cid,
            make_append_command(
                "task.submitted",
                expected_version=2, primitive_type="Task",
                payload={"artifacts": ["art_pr_001"]}, visibility="public",
            ),
            principal,
        )
        accepted = service.append_event(
            cid,
            make_append_command(
                "task.accepted",
                expected_version=3, primitive_type="Task",
                payload={}, visibility="public",
            ),
            principal,
        )
        assert started.current_status == "in_progress"
        assert submitted.current_status == "reviewing"
        assert accepted.current_status == "done"
        assert accepted.context_version == 4
        assert repo.collets[cid].context_version == 4

    def test_task_cancelled_from_created_by_principal(self):
        repo, service, created = _make_service()
        result = service.append_event(
            created.collab_context_id,
            make_append_command(
                "task.cancelled",
                primitive_type="Task", payload={}, visibility="public",
            ),
            make_actor(),
        )
        assert result.accepted is True
        assert result.current_status == "cancelled"
        assert result.context_version == 2

    def test_unregistered_workflow_kind_status_unchanged(self):
        """workflow_kind 不在 MACHINE_REGISTRY → 不裁决,status 不动。"""
        repo, service, created = _make_service(
            workflow_kind="forum_public_discussion"
        )
        result = service.append_event(
            created.collab_context_id,
            make_append_command(
                "task.started",
                primitive_type="Task", payload={}, visibility="public",
            ),
            make_actor(),
        )
        assert result.accepted is True
        assert result.context_version == 2
        assert result.current_status == "created"

    def test_event_without_matching_rule_status_unchanged(self):
        """registered 机器 + 已注册事件但当前状态无出边 → status 不动。"""
        repo, service, created = _make_service()
        result = service.append_event(
            created.collab_context_id,
            make_append_command(
                "task.submitted",
                primitive_type="Task",
                payload={"artifacts": ["art_pr_001"]}, visibility="public",
            ),
            make_actor(),
        )
        assert result.accepted is True
        assert result.current_status == "created"


class TestAppendEventRejections:
    """G4(service 层二道闸)与 G1 的拒绝路径。"""

    def test_unregistered_event_kind_rejected(self):
        repo, service, created = _make_service()
        command = make_append_command(
            "artifact.delivered", primitive_type="Activity", payload={}
        )
        with pytest.raises(ContextCollabValidationError, match="unregistered"):
            service.append_event(created.collab_context_id, command, make_actor())
        assert repo.append_calls == []

    def test_message_payload_with_content_ref_rejected(self):
        repo, service, created = _make_service()
        command = make_append_command(
            "user_interaction.posted",
            payload={"content_ref": "space://res/xxx"},
        )
        with pytest.raises(ContextCollabValidationError, match="content_ref"):
            service.append_event(created.collab_context_id, command, make_actor())
        assert repo.append_calls == []

    def test_visibility_out_of_vocabulary_rejected(self):
        repo, service, created = _make_service()
        command = make_append_command(
            "user_interaction.posted", visibility="team"
        )
        with pytest.raises(ContextCollabValidationError, match="visibility"):
            service.append_event(created.collab_context_id, command, make_actor())
        assert repo.append_calls == []

    def test_outsider_actor_rejected_by_g1(self):
        repo, service, created = _make_service()
        outsider = make_actor("human", "u_9999")
        command = make_append_command("task.started", actor_ref=outsider.actor)
        with pytest.raises(ContextCollabValidationError, match="executor may append"):
            service.append_event(created.collab_context_id, command, outsider)
        assert repo.append_calls == []

    def test_unknown_collab_context_not_found(self):
        repo, service, _ = _make_service()
        command = make_append_command("user_interaction.posted")
        with pytest.raises(ContextCollabNotFoundError):
            service.append_event("collet_missing", command, make_actor())
        assert repo.append_calls == []


class TestRepositorySemanticsPropagation:
    """parent / expected_version 由仓储把守,service 原样上抛。"""

    def test_version_conflict_propagates(self):
        repo, service, created = _make_service()
        command = make_append_command(
            "task.started",
            expected_version=99,
            primitive_type="Task",
            payload={},
            visibility="public",
        )
        with pytest.raises(ContextCollabVersionConflict):
            service.append_event(created.collab_context_id, command, make_actor())
        collet = repo.collets[created.collab_context_id]
        assert collet.context_version == 1
        assert len(repo.events[created.collab_context_id]) == 1

    def test_missing_parent_event_rejected(self):
        repo, service, created = _make_service()
        command = make_append_command(
            "user_interaction.posted", parent_event="evt_missing"
        )
        with pytest.raises(ContextCollabParentEventError):
            service.append_event(created.collab_context_id, command, make_actor())
        assert repo.collets[created.collab_context_id].context_version == 1


class TestAppendReduplication:
    def test_repeated_append_rejected_by_cas_not_replayed(self):
        """追加不设幂等键(spec 作者裁决):逐字重发被 ``expected_version``
        乐观并发拒绝(409,不落第二行);要追加新事件须带新版本号。"""
        repo, service, created = _make_service()
        cid = created.collab_context_id
        command = make_append_command(
            "task.started",
            primitive_type="Task",
            payload={},
            visibility="public",
        )
        first = service.append_event(cid, command, make_actor())
        assert first.accepted is True
        assert first.context_version == 2

        with pytest.raises(ContextCollabVersionConflict):
            service.append_event(cid, command, make_actor())
        assert len(repo.events[cid]) == 2

        second = service.append_event(
            cid,
            make_append_command(
                "task.started",
                expected_version=2,
                primitive_type="Task",
                payload={},
                visibility="public",
            ),
            make_actor(),
        )
        assert second.accepted is True
        assert second.event_id != first.event_id
        assert second.context_version == 3
        assert len(repo.events[cid]) == 3
