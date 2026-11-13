"""BBS 公开讨论产品层读用例单测(plan T4;spec §3.3 读路径)。

覆盖:
- 列表:两帖按 gmt_create 倒序、title/initiator_id/message_count(发言数
  含首帖正文)、未知 Collet 不计;

覆盖(详情):
- title 读源=初始事件 payload["objective"](NULL-payload 外部行回退
  伴随 context_spec.objective,创建事实 spec §3 裁决 1);
- body=首帖正文事件、replies 扁平有序带 parent 指针(楼中楼)、
  **只投影 public Message**(workspace 事件不得进读面);
- 孤儿帖(有标题无正文)body=None 不 422;
- 外 profile 404、未知 collet 404。

复用 write 测试的 fake 仓储模式(含列表/计数两方法的受控实现)。
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

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
    ContextSpec,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.products.bbs_public_discussion.service import (
    BbsPublicDiscussionService,
)
from tests.community.core.task.context_collab.product_catalog import PRODUCT_CATALOG

PRINCIPAL = PrincipalRef(principal_type="human", principal_id="u_1001")
SPACE = SpaceRef(space_id="space_workspace_001", environment="prod")


def _actor(principal_id: str = "u_1001") -> ActorContext:
    return ActorContext(actor=PrincipalRef("human", principal_id), source="api")


class _FakeRepo:
    """受控仓储:编排写三方法 + 列表/计数两读方法的受控实现。"""

    def __init__(self) -> None:
        self._status: dict[str, ContextStatusRecord] = {}

    # ---- 受写用例服务的最小实现(直插 record,绕开 create 流水) ----

    def add_record(
        self,
        record: ContextStatusRecord,
        gmt_create: datetime | None = None,
    ):
        self._status[record.collet.collet_id] = record
        if gmt_create is not None:
            self._gmt_create = getattr(self, "_gmt_create", {})
            self._gmt_create[record.collet.collet_id] = gmt_create
        return record

    def get_current_status(self, collab_context_id, *, trunk_events=False):
        return self._status.get(collab_context_id)

    def list_collets_by_product(self, product_kind: str):
        gmt = getattr(self, "_gmt_create", {})
        rows = []
        for record in self._status.values():
            if record.context_spec.product_kind != product_kind:
                continue
            rows.append(
                (
                    record.collet,
                    record.context_spec,
                    gmt.get(
                        record.collet.collet_id,
                        datetime.now(timezone.utc),
                    ),
                )
            )
        rows.sort(key=lambda row: row[2], reverse=True)
        return rows

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
        raise NotImplementedError("read tests use add_record")

    def append_event(self, collab_context_id, request):
        raise NotImplementedError("read tests use add_record")

    # ---- 构造助手 ----

    def event(
        self,
        collab_context_id: str,
        kind: str = "user_interaction.posted",
        primitive: str = "Message",
        visibility: str | None = "public",
        parent: str | None = None,
        payload: dict | None = None,
        actor_id: str = "u_1001",
        occurred_at: datetime | None = None,
    ) -> EventRecord:
        if payload is None:
            payload = {"content": "回帖内容"}
        return EventRecord(
            collab_context_id=collab_context_id,
            event_id="evt_" + uuid4().hex[:8],
            parent_event=parent,
            event_kind=kind,
            primitive_type=primitive,
            actor_ref=PrincipalRef("human", actor_id),
            visibility=visibility,
            payload=payload,
            context_version_after=2,
            occurred_at=occurred_at or datetime(2026, 11, 12, 8, 0, 0),
        )

    def collet(self, collab_context_id: str, state: ColletState = ColletState.ACTIVE):
        return Collet(
            collet_id=collab_context_id,
            initiator_ref=PRINCIPAL,
            state=state,
            scope=SPACE,
            current_event_id=None,
            version=1,
        )

    @staticmethod
    def bbs_spec(objective: str) -> ContextSpec:
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

    @staticmethod
    def todo_spec() -> ContextSpec:
        return ContextSpec(
            space=SPACE,
            principal_ref=PRINCIPAL,
            method_kind="Single",
            executor_ref=None,
            objective="团队待办",
            resource_refs=(),
            product_kind="team_todo",
            workflow_kind="team_todo_execution",
        )


def _service(repo: _FakeRepo) -> BbsPublicDiscussionService:
    return BbsPublicDiscussionService(
        collab_repo=repo,
        authorizer=StructuralContextCollabAuthorizer(),
        method_store=InMemoryMethodStore(),
        product_catalog=PRODUCT_CATALOG,
    )


def _discussion(
    repo: _FakeRepo,
    collet_id: str,
    objective: str,
    *,
    with_body: bool = True,
    gmt_create: datetime | None = None,
    extra_events: list[EventRecord] | None = None,
) -> ContextStatusRecord:
    events: list[EventRecord] = [
        EventRecord(
            collab_context_id=collet_id,
            event_id="evt_created_" + collet_id[-6:],
            parent_event=None,
            event_kind="task.created",
            primitive_type="Task",
            actor_ref=PRINCIPAL,
            visibility="public",
            payload={"objective": objective, "resource_refs": []},
            context_version_after=1,
            occurred_at=datetime(2026, 11, 12, 7, 0, 0),
        )
    ]
    if with_body:
        events.append(
            repo.event(
                collet_id,
                payload={"content": objective + " 的正文"},
                occurred_at=datetime(2026, 11, 12, 7, 1, 0),
            )
        )
    if extra_events:
        events.extend(extra_events)
    record = ContextStatusRecord(
        collet=repo.collet(collet_id),
        context_spec=repo.bbs_spec(objective),
        current_snapshot=None,
        events=events,
    )
    return repo.add_record(record, gmt_create=gmt_create)


class TestListDiscussions:
    def test_orders_desc_with_counts_and_initiator(self):
        repo = _FakeRepo()
        older_id = "collet_list_older_0001"
        newer_id = "collet_list_newer_0002"
        _discussion(
            repo,
            older_id,
            "早帖标题",
            gmt_create=datetime(2026, 11, 12, 8, 0, 0),
            extra_events=[repo.event(older_id)],
        )
        _discussion(
            repo,
            newer_id,
            "晚帖标题",
            gmt_create=datetime(2026, 11, 12, 9, 0, 0),
        )

        views = _service(repo).list_discussions()

        assert [v.collet_id for v in views] == [newer_id, older_id]
        first = views[0]
        assert first.title == "晚帖标题"
        assert first.initiator_id == "u_1001"
        assert first.message_count == 1  # 含首帖正文
        assert views[1].message_count == 2  # 正文 + 一条回帖

    def test_list_only_bbs_profile(self):
        repo = _FakeRepo()
        record = ContextStatusRecord(
            collet=repo.collet("collet_todo_row_0003"),
            context_spec=_FakeRepo.todo_spec(),
            current_snapshot=None,
            events=[],
        )
        repo.add_record(record, gmt_create=datetime(2026, 11, 12, 10, 0, 0))
        _discussion(repo, "collet_bbs_only_0004", "只有我")

        views = _service(repo).list_discussions()

        assert [v.collet_id for v in views] == ["collet_bbs_only_0004"]


class TestGetDiscussion:
    def test_assembles_title_body_and_replies_with_parent_chain(self):
        repo = _FakeRepo()
        collet_id = "collet_detail_0001"
        body = repo.event(
            collet_id, payload={"content": "正文内容A"}, actor_id="u_1001"
        )
        reply_r1 = repo.event(
            collet_id, payload={"content": "一层回帖"}, actor_id="u_2002"
        )
        reply_r2 = repo.event(
            collet_id,
            payload={"content": "楼中楼"},
            parent=body.event_id,
            actor_id="u_3003",
        )
        _discussion(
            repo,
            collet_id,
            "详情标题",
            with_body=False,
            extra_events=[body, reply_r1, reply_r2],
        )

        detail = _service(repo).get_discussion(collet_id)

        assert detail.collet_id == collet_id
        assert detail.title == "详情标题"
        assert detail.body is not None
        assert detail.body.content == "正文内容A"
        assert detail.body.author_id == "u_1001"
        assert [r.event_id for r in detail.replies] == [
            reply_r1.event_id,
            reply_r2.event_id,
        ]
        assert detail.replies[0].parent_event_id is None
        assert detail.replies[1].parent_event_id == body.event_id
        assert detail.replies[1].content == "楼中楼"

    def test_title_falls_back_to_context_spec_for_null_payload_row(self):
        """NULL-payload 外部行:初始事件无 payload → 回退伴随 spec(P3 撤列时移除)。"""
        repo = _FakeRepo()
        collet_id = "collet_null_payload_01"
        record = ContextStatusRecord(
            collet=repo.collet(collet_id),
            context_spec=_FakeRepo.bbs_spec("外部行的标题"),
            current_snapshot=None,
            events=[
                EventRecord(
                    collab_context_id=collet_id,
                    event_id="evt_created_null_01",
                    parent_event=None,
                    event_kind="task.created",
                    primitive_type="Task",
                    actor_ref=PRINCIPAL,
                    visibility="public",
                    payload=None,
                    context_version_after=1,
                    occurred_at=datetime(2026, 11, 12, 7, 0, 0),
                )
            ],
        )
        repo.add_record(record)

        detail = _service(repo).get_discussion(collet_id)

        assert detail.title == "外部行的标题"
        assert detail.body is None

    def test_only_projects_public_messages(self):
        """workspace 事件(经通用路由写入)不得进公开讨论读面。"""
        repo = _FakeRepo()
        collet_id = "collet_visibility_01"
        body = repo.event(collet_id, payload={"content": "正文"})
        public_reply = repo.event(collet_id, payload={"content": "公开回帖"})
        workspace_reply = repo.event(
            collet_id, payload={"content": "工作区事件"}, visibility="workspace"
        )
        private_reply = repo.event(
            collet_id, payload={"content": "私有事件"}, visibility="private"
        )
        _discussion(
            repo,
            collet_id,
            "可见性标题",
            with_body=False,
            extra_events=[body, public_reply, workspace_reply, private_reply],
        )

        detail = _service(repo).get_discussion(collet_id)

        assert [r.content for r in detail.replies] == ["公开回帖"]

    def test_tolerates_bodyless_collet(self):
        """发帖两步窗口的孤儿行:详情 body=None,不 422(spec §6-D)。"""
        repo = _FakeRepo()
        _discussion(repo, "collet_orphan_00001", "有标题无正文", with_body=False)

        detail = _service(repo).get_discussion("collet_orphan_00001")

        assert detail.title == "有标题无正文"
        assert detail.body is None
        assert detail.replies == []

    def test_rejects_foreign_profile(self):
        repo = _FakeRepo()
        record = ContextStatusRecord(
            collet=repo.collet("collet_foreign_0001"),
            context_spec=_FakeRepo.todo_spec(),
            current_snapshot=None,
            events=[],
        )
        repo.add_record(record)

        with pytest.raises(ContextCollabNotFoundError):
            _service(repo).get_discussion("collet_foreign_0001")

    def test_unknown_collet_not_found(self):
        repo = _FakeRepo()
        with pytest.raises(ContextCollabNotFoundError):
            _service(repo).get_discussion("collet_missing_404")
