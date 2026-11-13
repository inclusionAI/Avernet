"""BBS 公开讨论产品层写用例单测(plan T3;spec §3.3)。

覆盖:
- 发帖:spec 编织(L6/L5 取值、objective=标题)、初始事件 payload=
  ``creation_payload`` 六键、首帖正文事件(expected_version=1、public
  Message、parent=None)、L5 顶层 ACTIVE MethodBinding、视图四字段;
- 回帖:None parent=回主题、楼中楼挂正文事件、G1 窄规则陌生 actor 放行、
  ``task.created`` 骨架不可当 parent(产品域断言)、外 profile 404、
  collet 不存在 404、``expected_version`` CAS 冲突透传;
- 创建空标题/空正文拒绝。

fake 仓储为受控 in-memory(实现协议五方法,列表/计数不进本测试面);
store 用真 ``InMemoryMethodStore``。
"""

import dataclasses
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
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
    ContextSpec,
    CurrentSnapshot,
    ProgressSnapshot,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.creation_facts import (
    creation_payload,
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
    AppendEventRecordRequest,
    ContextCreatedRecord,
    ContextStatusRecord,
    CreateContextRecordRequest,
    EventAppendedRecord,
    InitialEventDraft,
)
from agentclaw.community.core.task.context_collab.products.bbs_public_discussion.service import (
    BbsPublicDiscussionService,
)
from agentclaw.community.core.task.context_collab.products.service_api import (
    CreateDiscussionCommand,
    ReplyDiscussionCommand,
)

PRINCIPAL = PrincipalRef(principal_type="human", principal_id="u_1001")
SPACE = SpaceRef(space_id="space_workspace_001", environment="prod")
TITLE = "Collet 聚合根对齐 ACCS 的落地评审"
BODY = "正文:按 spec §3 逐字段核对。"


def _actor(principal_id: str = "u_1001") -> ActorContext:
    return ActorContext(actor=PrincipalRef("human", principal_id), source="api")


def _bot_actor(agent_id: str = "bot_comment_001") -> ActorContext:
    """L2 词汇:agent 作为已认证调用者 = PrincipalRef("bot", <agent_id>)。

    BBS 场景中 Bot 读到帖子后公开补充意见即走此形态;``principal_id``
    即 agent_id(G1 ``_is_executor`` 先例按字符串相等匹配)。
    """
    return ActorContext(
        actor=PrincipalRef("bot", agent_id), source="api"
    )


class _FakeRepo:
    """受控 in-memory 仓储:协作编排三方法可用,列表/计数不进本测试面。"""

    def __init__(self) -> None:
        self._status: dict[str, ContextStatusRecord] = {}
        self.create_calls: list[CreateContextRecordRequest] = []
        self.append_calls: list[AppendEventRecordRequest] = []
        self.conflict_on_append = False

    def create_context(self, request: CreateContextRecordRequest):
        self.create_calls.append(request)
        collab_context_id = "collet_" + uuid4().hex
        event_id = "evt_" + uuid4().hex
        now = datetime.now(timezone.utc)
        snapshot = CurrentSnapshot(
            objective=request.context_spec.objective,
            status=TaskStatus.CREATED.value,
            principal_ref=request.context_spec.principal_ref,
            executor_ref=request.context_spec.executor_ref,
            method_kind=request.context_spec.method_kind,
            progress=ProgressSnapshot(),
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
            occurred_at=now,
        )
        self._status[collab_context_id] = ContextStatusRecord(
            collet=collet,
            context_spec=request.context_spec,
            current_snapshot=snapshot,
            events=[initial_event],
        )
        return ContextCreatedRecord(
            collet=collet,
            context_spec=request.context_spec,
            current_snapshot=snapshot,
            initial_event=initial_event,
        )

    def append_event(self, collab_context_id, request: AppendEventRecordRequest):
        if self.conflict_on_append:
            raise ContextCollabVersionConflict("stale expected_version")
        record = self._status[collab_context_id]
        event_id = "evt_" + uuid4().hex
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
        self.append_calls.append(request)
        new_snapshot = (
            request.snapshot_updater(record.current_snapshot)
            if record.current_snapshot is not None
            else None
        )
        new_collet = dataclasses.replace(
            record.collet,
            version=record.collet.version + 1,
            current_event_id=event_id,
        )
        self._status[collab_context_id] = ContextStatusRecord(
            collet=new_collet,
            context_spec=record.context_spec,
            current_snapshot=new_snapshot,
            events=[*record.events, event],
        )
        return EventAppendedRecord(
            event=event,
            collet=new_collet,
            context_spec=record.context_spec,
            current_snapshot=new_snapshot,
        )

    def get_current_status(self, collab_context_id, *, trunk_events=False):
        return self._status.get(collab_context_id)

    def list_collets_by_product(self, product_kind: str):
        raise NotImplementedError("fake does not implement product listing")

    def count_message_events(self, collab_context_ids):
        raise NotImplementedError("fake does not implement message counting")

    # 测试助手:事件流查询。
    def event_id_of(self, collab_context_id: str, primitive: str) -> str:
        record = self._status[collab_context_id]
        return next(
            e.event_id
            for e in record.events
            if e.primitive_type == primitive
        )


def _make_service(repo: _FakeRepo | None = None):
    repo = repo or _FakeRepo()
    store = InMemoryMethodStore()
    service = BbsPublicDiscussionService(
        collab_repo=repo,
        authorizer=StructuralContextCollabAuthorizer(),
        method_store=store,
    )
    return service, repo, store


def _top_level_binding(store, repo, collab_context_id):
    """经 L5 自愈缝读回顶层 binding(已投影则幂等返回既有)。"""
    from agentclaw.community.core.task.context_collab.services.method_lifecycle_service import (
        _now,
        current_axes,
        ensure_top_level_binding,
    )

    return ensure_top_level_binding(
        store,
        current_axes(),
        collab_context_id,
        repo,
        _actor(),
        now=_now(),
    )


class TestCreateDiscussion:
    def test_wires_full_creation_facts_and_body_event(self):
        service, repo, store = _make_service()

        view = service.create_discussion(
            CreateDiscussionCommand(title=TITLE, body=BODY, space=SPACE),
            _actor(),
        )

        request = repo.create_calls[0]
        spec = request.context_spec
        assert spec.objective == TITLE
        assert spec.method_kind == "Open + Group"
        assert spec.product_kind == "bbs_public_discussion"
        assert spec.workflow_kind == "bbs_public_discussion"
        assert spec.executor_ref is None
        assert request.initial_event.event_kind == "task.created"
        assert request.initial_event.payload == creation_payload(spec, TITLE)

        assert len(repo.append_calls) == 1
        append = repo.append_calls[0]
        assert append.expected_version == 1
        assert append.event_kind == "user_interaction.posted"
        assert append.primitive_type == "Message"
        assert append.visibility == "public"
        assert append.payload == {"content": BODY}

        binding = _top_level_binding(store, repo, view.collet_id)
        assert binding.method_kind == "Open + Group"
        assert binding.state is BindingState.ACTIVE

        assert view.title == TITLE
        assert view.body_event_id == repo.event_id_of(view.collet_id, "Message")
        assert view.context_version == 2

    def test_rejects_blank_title_and_blank_body(self):
        service, repo, _ = _make_service()
        with pytest.raises(ContextCollabValidationError):
            service.create_discussion(
                CreateDiscussionCommand(title=" ", body=BODY, space=SPACE),
                _actor(),
            )
        with pytest.raises(ContextCollabValidationError):
            service.create_discussion(
                CreateDiscussionCommand(title=TITLE, body="", space=SPACE),
                _actor(),
            )
        assert repo.create_calls == []
        assert repo.append_calls == []


class TestReplyDiscussion:
    def test_reply_to_topic_then_nested_reply(self):
        service, repo, _ = _make_service()
        created = service.create_discussion(
            CreateDiscussionCommand(title=TITLE, body=BODY, space=SPACE), _actor()
        )

        first = service.reply_discussion(
            ReplyDiscussionCommand(
                collab_context_id=created.collet_id, content="第一层回帖"
            ),
            _actor("u_2002"),
        )
        assert first.parent_event_id is None
        assert first.collab_context_id == created.collet_id

        body_event_id = repo.event_id_of(created.collet_id, "Message")
        nested = service.reply_discussion(
            ReplyDiscussionCommand(
                collab_context_id=created.collet_id,
                content="楼中楼回复正文",
                parent_event_id=body_event_id,
            ),
            _actor(),
        )
        assert nested.parent_event_id == body_event_id

    def test_reply_rejects_skeleton_event_parent(self):
        """骨架事件在流内可过仓储存在性——产品域断言必须拒。"""
        service, repo, _ = _make_service()
        created = service.create_discussion(
            CreateDiscussionCommand(title=TITLE, body=BODY, space=SPACE), _actor()
        )
        skeleton = repo._status[created.collet_id].events[0]
        assert skeleton.event_kind == "task.created"

        with pytest.raises(ContextCollabValidationError):
            service.reply_discussion(
                ReplyDiscussionCommand(
                    collab_context_id=created.collet_id,
                    content="挂到骨架上",
                    parent_event_id=skeleton.event_id,
                ),
                _actor(),
            )

    def test_reply_rejects_foreign_profile_collet(self):
        service, repo, _ = _make_service()
        spec = ContextSpec(
            space=SPACE,
            principal_ref=PRINCIPAL,
            method_kind="Single",
            executor_ref=None,
            objective="Single 待办(测试用途,绕过 check_create)",
            resource_refs=(),
            product_kind="team_todo",
            workflow_kind="team_todo_execution",
        )
        created = repo.create_context(
            CreateContextRecordRequest(
                context_spec=spec,
                initial_event=InitialEventDraft(
                    event_kind="task.created",
                    primitive_type="Task",
                    actor_ref=PRINCIPAL,
                    payload={"objective": spec.objective, "resource_refs": []},
                ),
            )
        )

        with pytest.raises(ContextCollabNotFoundError):
            service.reply_discussion(
                ReplyDiscussionCommand(
                    collab_context_id=created.collet.collet_id,
                    content="串门回帖",
                ),
                _actor(),
            )

    def test_reply_unknown_collet_not_found(self):
        service, _, _ = _make_service()
        with pytest.raises(ContextCollabNotFoundError):
            service.reply_discussion(
                ReplyDiscussionCommand(
                    collab_context_id="collet_missing_404", content="x"
                ),
                _actor(),
            )

    def test_reply_version_conflict_propagates(self):
        service, repo, _ = _make_service()
        created = service.create_discussion(
            CreateDiscussionCommand(title=TITLE, body=BODY, space=SPACE), _actor()
        )
        repo.conflict_on_append = True

        with pytest.raises(ContextCollabVersionConflict):
            service.reply_discussion(
                ReplyDiscussionCommand(
                    collab_context_id=created.collet_id, content="x"
                ),
                _actor(),
            )

    def test_reply_allows_bot_agent_actor(self):
        """Bot/Agent 也是回帖一等主体:PrincipalRef("bot", agent_id) 经 G1
        窄规则(任意已认证 actor 可发 public Message)放行,落库 actor_ref
        即 bot 身份——BBS 公开讨论的 Agent 补充意见即此形态。"""
        service, repo, _ = _make_service()
        created = service.create_discussion(
            CreateDiscussionCommand(title=TITLE, body=BODY, space=SPACE),
            _actor(),
        )

        view = service.reply_discussion(
            ReplyDiscussionCommand(
                collab_context_id=created.collet_id,
                content="Bot 补充:评审结论第 3 条已核对为误。",
            ),
            _bot_actor("bot_comment_001"),
        )

        assert view is not None
        record = repo._status[created.collet_id]
        replied = record.events[-1]
        assert replied.actor_ref.principal_type == "bot"
        assert replied.actor_ref.principal_id == "bot_comment_001"

    def test_reply_allows_stranger_authenticated_actor(self):
        """G1 窄规则:Open+Group 下任意已认证 actor 可发 public Message。"""
        service, repo, _ = _make_service()
        created = service.create_discussion(
            CreateDiscussionCommand(title=TITLE, body=BODY, space=SPACE), _actor()
        )

        view = service.reply_discussion(
            ReplyDiscussionCommand(
                collab_context_id=created.collet_id, content="路人回帖"
            ),
            _actor("u_stranger_not_in_spec"),
        )
        assert view is not None
        record = repo._status[created.collet_id]
        assert record.events[-1].actor_ref.principal_id == "u_stranger_not_in_spec"
