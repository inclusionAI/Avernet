"""Context collab 服务查询用例单测(Task 11)。

覆盖 ``ContextCollabService.get_status``(spec §6.4 指定响应示例):
G3 投影(事件状态推导 + 进度统计,Message 不计数)、
``trunk_events`` 只过滤不改变快照/当前事件/统计(§12.2 #4)、
事件条目无 ``steps`` 字段(§12.2 #5)、G1 visibility 过滤在投影前
(非 principal/executor 只见 public 事件,统计只反映可见事件)、
纯读路径不推进 ``context_version`` 与未知上下文 NotFound。
仓储用手写 in-memory dict fake,不触碰数据库。
"""

from datetime import datetime, timezone

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
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
    ContextSpec,
    CurrentSnapshot,
    ProgressSnapshot,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepositoryProtocol,
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
    ContextCollabServiceProtocol,
    ContextCollabStatusProjection,
    TaskProgressProjectionDTO,
)
from agentclaw.community.core.task.context_collab.services.context_collab_service import (
    ContextCollabService,
)

COLLET_ID = "collet_001"
OBJECTIVE = "整理 Context 协作协议评审结论并提交可评审 PR"
PRINCIPAL = PrincipalRef(principal_type="human", principal_id="u_1001")
EXECUTOR = AgentRef(agent_type="bot", agent_id="bot_coding_001")
OCCURRED_AT = datetime(2026, 11, 10, 8, 30, 0, tzinfo=timezone.utc)


def make_actor(principal_id: str = "u_1001") -> ActorContext:
    return ActorContext(PrincipalRef("human", principal_id), source="api")


def _spec() -> ContextSpec:
    return ContextSpec(
        space=SpaceRef(space_id="space_workspace_001", environment="prod"),
        principal_ref=PRINCIPAL,
        method_kind="Single",
        executor_ref=EXECUTOR,
        objective=OBJECTIVE,
        resource_refs=(),
        product_kind=None,
        workflow_kind=None,
    )


def _snapshot() -> CurrentSnapshot:
    """§6.4 示例形态的落库快照:status=in_progress、progress 全 0 初值。

    落库的 snapshot progress 保持创建时的全 0 初值(stale),
    查询投影必须用 build_progress 的实时值替换它。
    """
    return CurrentSnapshot(
        objective=OBJECTIVE,
        status="in_progress",
        principal_ref=PRINCIPAL,
        executor_ref=EXECUTOR,
        method_kind="Single",
        progress=ProgressSnapshot(percent=0, current_event_id=None),
    )


def _collet(*, current_event_id: str = "evt_004") -> Collet:
    """§6.4 示例形态的聚合根(current=evt_004);spec/快照是 record 伴随投影。"""
    return Collet(
        collet_id=COLLET_ID,
        initiator_ref=PRINCIPAL,
        state=ColletState.ACTIVE,
        scope=_spec().space,
        current_event_id=current_event_id,
        version=4,
    )


def _event(
    event_id: str,
    *,
    event_kind: str,
    primitive_type: str,
    parent_event: str | None,
    visibility: str = "public",
    payload: dict | None = None,
) -> EventRecord:
    """最小事件构造器;列表顺序即仓储 id 升序(接纳顺序)。"""
    return EventRecord(
        collab_context_id=COLLET_ID,
        event_id=event_id,
        parent_event=parent_event,
        event_kind=event_kind,
        primitive_type=primitive_type,
        actor_ref=PRINCIPAL,
        visibility=visibility,
        payload=dict(payload or {}),
        context_version_after=1,
        occurred_at=OCCURRED_AT,
    )


def _fixture_events() -> list[EventRecord]:
    """spec §6.4 示例的 5 事件链:Task, Message, Task, Activity, Activity。

    evt_002(Message)为 workspace、evt_003(Task)为 private,为
    visibility 过滤用例提供素材;principal 视角 5 条全可见。
    """
    return [
        _event(
            "evt_001",
            event_kind="task.created",
            primitive_type="Task",
            parent_event=None,
            payload={"objective": OBJECTIVE, "summary": "创建协作任务"},
        ),
        _event(
            "evt_002",
            event_kind="user_interaction.posted",
            primitive_type="Message",
            parent_event="evt_001",
            visibility="workspace",
            payload={
                "message_kind": "user_interaction",
                "content": "上下文做一下补充xxx",
            },
        ),
        _event(
            "evt_003",
            event_kind="task.assigned",
            primitive_type="Task",
            parent_event="evt_002",
            visibility="private",
            payload={"summary": "指定 bot_coding_001 执行"},
        ),
        _event(
            "evt_004",
            event_kind="task.progress.updated",
            primitive_type="Activity",
            parent_event="evt_003",
            payload={"summary": "开始整理协议设计", "percent": 40},
        ),
        _event(
            "evt_005",
            event_kind="task.progress.updated",
            primitive_type="Activity",
            parent_event="evt_004",
            payload={"summary": "完成设计并准备提交 PR"},
        ),
    ]


class _FakeStatusRepository:

    """手写 in-memory dict 仓储 fake,只实现 ``get_current_status`` 的
    真实语义:collet 行 + events 按接纳顺序(自增 id)全量返回,
    不存在返回 ``None``;trunk 过滤在服务投影层完成。
    ``get_calls`` 记录读取入参,用于断言服务只读不写。
    """

    def list_collets_by_product(self, product_kind: str):
        raise NotImplementedError("fake does not implement product listing")

    def count_message_events(self, collab_context_ids):
        raise NotImplementedError("fake does not implement message counting")


    def __init__(
        self,
        collet: Collet,
        snapshot: CurrentSnapshot | None,
        events: list[EventRecord],
    ) -> None:
        self.collets: dict[str, Collet] = {collet.collet_id: collet}
        self.snapshots: dict[str, CurrentSnapshot | None] = {
            collet.collet_id: snapshot
        }
        self.events: dict[str, list[EventRecord]] = {
            collet.collet_id: list(events)
        }
        self.get_calls: list[str] = []
        self.write_calls: list[tuple] = []

    def get_current_status(
        self, collab_context_id: str
    ) -> "ContextStatusRecord | None":
        self.get_calls.append(collab_context_id)
        collet = self.collets.get(collab_context_id)
        if collet is None:
            return None
        events = list(self.events.get(collab_context_id, []))
        return ContextStatusRecord(
            collet=collet,
            context_spec=_spec(),
            current_snapshot=self.snapshots[collab_context_id],
            events=events,
        )

    # 写路径在查询用例中不允许被触达;若被调用记录并报错。
    def create_context(self, request) -> None:
        self.write_calls.append(("create_context", request))
        raise NotImplementedError("get_status must not write")

    def append_event(self, collab_context_id, request) -> None:
        self.write_calls.append(("append_event", collab_context_id, request))
        raise NotImplementedError("get_status must not write")


def _make_service(
    *, collet: Collet | None = None, events: list[EventRecord] | None = None
):
    repo = _FakeStatusRepository(
        collet or _collet(), _snapshot(), events or _fixture_events()
    )
    assert isinstance(repo, ContextCollabRepositoryProtocol)
    service = ContextCollabService(
            method_store=InMemoryMethodStore(),
            repository=repo,
 )
    assert isinstance(service, ContextCollabServiceProtocol)
    return repo, service


class TestGetStatusHappyPath:
    """spec §6.4 指定响应示例的投影组装。"""

    def test_snapshot_progress_reflects_projected_events(self):
        repo, service = _make_service()
        result = service.get_status(
            COLLET_ID, trunk_events=False, context=make_actor()
        )
        assert isinstance(result, ContextCollabStatusProjection)
        assert result.collab_context_id == COLLET_ID
        assert result.context_version == 4

        snapshot = result.current_snapshot
        assert snapshot["objective"] == OBJECTIVE
        assert snapshot["status"] == "in_progress"
        progress = snapshot["progress"]
        # 落库快照的 progress 是全 0 初值,查询必须用实时投影替换。
        assert repo.snapshots[COLLET_ID].progress.percent == 0
        assert progress["percent"] == 40
        # completed 只计非 Message 事件:evt_001/evt_003 两个 Task;
        # evt_002(Message)不计数(spec §6.4 示例 completed_count=2)。
        assert progress["completed_count"] == 2
        assert progress["active_count"] == 1
        assert progress["blocked_count"] == 0
        # current_event_id 沿用 collet 行值,不重新推导(≠ evt_005)。
        assert progress["current_event_id"] == "evt_004"

    def test_current_event_keyed_by_collet_pointer(self):
        _, service = _make_service()
        result = service.get_status(
            COLLET_ID, trunk_events=False, context=make_actor()
        )
        current_event = result.current_event
        assert current_event is not None
        assert current_event["event_id"] == "evt_004"
        assert current_event["parent_event"] == "evt_003"
        assert current_event["event_kind"] == "task.progress.updated"
        assert current_event["primitive_type"] == "Activity"
        assert current_event["summary"] == "开始整理协议设计"
        # current 的状态由 collet.current_event_id 决定:evt_004 → active。
        assert current_event["status"] == "active"
        assert current_event["occurred_at"] == OCCURRED_AT

    def test_task_progress_events_projection(self):
        _, service = _make_service()
        result = service.get_status(
            COLLET_ID, trunk_events=False, context=make_actor()
        )
        task_progress = result.task_progress
        assert isinstance(task_progress, TaskProgressProjectionDTO)
        assert task_progress.total_events == 5
        assert [
            entry["event_id"] for entry in task_progress.events
        ] == ["evt_001", "evt_002", "evt_003", "evt_004", "evt_005"]

        statuses = {
            entry["event_id"]: entry["status"] for entry in task_progress.events
        }
        # 前三条 completed、evt_004 active、末条 pending(§6.4)。
        assert statuses == {
            "evt_001": "completed",
            "evt_002": "completed",
            "evt_003": "completed",
            "evt_004": "active",
            "evt_005": "pending",
        }
        message = task_progress.events[1]
        assert message["content"] == "上下文做一下补充xxx"
        assert message["primitive_type"] == "Message"
        # §12.2 #5:条目字段集合不含 "steps"。
        for entry in task_progress.events:
            assert "steps" not in entry
            assert set(entry) == {
                "event_id",
                "parent_event",
                "event_kind",
                "primitive_type",
                "status",
                "summary",
                "content",
            }
        assert task_progress.events[0]["summary"] == "创建协作任务"
        assert task_progress.events[0]["parent_event"] is None
        assert task_progress.events[4]["parent_event"] == "evt_004"

    def test_unknown_collab_context_not_found(self):
        _, service = _make_service()
        with pytest.raises(ContextCollabNotFoundError):
            service.get_status(
                "collet_missing", trunk_events=False, context=make_actor()
            )


class TestTrunkFilterIdentity:
    """§12.2 #4 / Controller Ruling R6:trunk_events 只过滤
    task_progress.events[] 条目,不改 current_snapshot / current_event /
    统计 / percent —— 即使分支事件携带 percent、计入统计、甚至充当
    current 指针。"""

    @staticmethod
    def _branch_event(**overrides) -> EventRecord:
        """挂在 evt_004 下的派生事件;字段可覆盖。

        Ruling R9:is_trunk 不落库。default collet current=evt_004 → 活动
        链为 evt_001..evt_004,本事件在链外;恢复链视图时它被过滤。
        """
        fields = {
            "event_id": "evt_006",
            "event_kind": "task.progress.updated",
            "primitive_type": "Activity",
            "parent_event": "evt_004",
            "payload": {"summary": "主干外的分支补充"},
        }
        fields.update(overrides)
        return _event(**fields)

    def test_trunk_flag_filters_events_only(self):
        _, service = _make_service(
            events=_fixture_events() + [self._branch_event()]
        )
        full = service.get_status(
            COLLET_ID, trunk_events=False, context=make_actor()
        )
        trunk = service.get_status(
            COLLET_ID, trunk_events=True, context=make_actor()
        )

        assert full.task_progress.total_events == 6
        # Ruling R9:主干 = current(evt_004)沿 parent 回溯的活动链;
        # 链后的 pending 事件(evt_005)与链外分支(evt_006)都被滤除。
        assert trunk.task_progress.total_events == 4
        assert [entry["event_id"] for entry in trunk.task_progress.events] == [
            "evt_001", "evt_002", "evt_003", "evt_004",
        ]
        # 两次调用的 current_snapshot、current_event、percent 完全相等。
        assert full.current_snapshot == trunk.current_snapshot
        assert full.current_event == trunk.current_event
        assert trunk.current_snapshot["progress"]["percent"] == 40
        assert trunk.current_snapshot["progress"]["completed_count"] == 2
        assert trunk.current_snapshot["progress"]["active_count"] == 1
        assert trunk.current_event["event_id"] == "evt_004"

    def test_branch_percent_does_not_drive_trunk_percent(self):
        """分支事件携带 percent=80:percent 从全量可见事件推导,
        不因 trunk 开关翻回主干上的 40。"""
        _, service = _make_service(
            events=_fixture_events()
            + [self._branch_event(payload={"summary": "分支提交更多进展",
                                           "percent": 80})]
        )
        full = service.get_status(
            COLLET_ID, trunk_events=False, context=make_actor()
        )
        trunk = service.get_status(
            COLLET_ID, trunk_events=True, context=make_actor()
        )

        assert full.task_progress.total_events == 6
        assert [
            entry["event_id"] for entry in trunk.task_progress.events
        ] == ["evt_001", "evt_002", "evt_003", "evt_004"]
        assert full.current_snapshot["progress"]["percent"] == 80
        assert trunk.current_snapshot["progress"]["percent"] == 80

    def test_branch_completion_counts_do_not_shift(self):
        """已完成分支事件(current 之前、非 Message):统计从全量可见
        事件推导,completed_count 不因 trunk 开关回退。"""
        fixture = _fixture_events()
        events = [
            fixture[0],  # evt_001 Task completed
            fixture[1],  # evt_002 workspace Message(不计数)
            self._branch_event(
                event_id="evt_branch_003",
                parent_event="evt_002",
                payload={"summary": "分支上的活动已完成"},
            ),
            fixture[2],  # evt_003 Task completed(承接链)
            fixture[3],  # evt_004 Activity active(current)
            fixture[4],  # evt_005 Activity pending
        ]
        _, service = _make_service(events=events)
        full = service.get_status(
            COLLET_ID, trunk_events=False, context=make_actor()
        )
        trunk = service.get_status(
            COLLET_ID, trunk_events=True, context=make_actor()
        )

        # 全量推导把分支活动也算进 completed(evt_001/e3/分支);
        # 两个开关都必须报告全量推导值(Ruling R6)。
        assert full.current_snapshot["progress"]["completed_count"] == 3
        assert trunk.current_snapshot["progress"]["completed_count"] == 3
        assert full.current_snapshot["progress"] == (
            trunk.current_snapshot["progress"]
        )
        # 活动链 = evt_001..evt_004(分支在链外)。
        assert trunk.task_progress.total_events == 4

    def test_current_pointer_on_branch_event_stays_projected(self):
        """current 指针落在分支事件上(最强场景):current_event、事件
        状态、统计在两个开关下完全一致,主干条目不退化成全 pending。"""
        _, service = _make_service(
            collet=_collet(current_event_id="evt_006"),
            events=_fixture_events() + [self._branch_event()],
        )
        full = service.get_status(
            COLLET_ID, trunk_events=False, context=make_actor()
        )
        trunk = service.get_status(
            COLLET_ID, trunk_events=True, context=make_actor()
        )

        # current_event 都指向分支事件且为 active,不因 trunk 开关消失。
        assert full.current_event == trunk.current_event
        assert full.current_event is not None
        assert full.current_event["event_id"] == "evt_006"
        assert full.current_event["status"] == "active"

        # 统计一致:current 之前的全部非 Message 事件(evt_001/003/004/005)
        # 都计入 completed,分支事件计入 active。
        assert full.current_snapshot == trunk.current_snapshot
        progress = full.current_snapshot["progress"]
        assert progress["completed_count"] == 4
        assert progress["active_count"] == 1
        assert progress["blocked_count"] == 0
        assert progress["current_event_id"] == "evt_006"

        # Ruling R9:current 指在分支上时,分支就在活动链上;
        # 链 = evt_006 → evt_004 → … → evt_001,链外的 evt_005 被滤除。
        # 列出的主干条目状态与全量视图一致,不退化成全 pending。
        assert [
            entry["event_id"] for entry in trunk.task_progress.events
        ] == ["evt_001", "evt_002", "evt_003", "evt_004", "evt_006"]
        assert [
            (entry["event_id"], entry["status"])
            for entry in trunk.task_progress.events
        ] == [
            (entry["event_id"], entry["status"])
            for entry in full.task_progress.events
            if entry["event_id"] != "evt_005"
        ]


class TestVisibilityFiltering:
    """G1 visibility 过滤在投影前生效:非 principal/executor 只见
    public 事件,统计与 current_event 也只反映可见事件。"""

    def test_outsider_sees_public_events_only(self):
        _, service = _make_service()
        result = service.get_status(
            COLLET_ID, trunk_events=False, context=make_actor("u_9999")
        )
        assert [
            entry["event_id"] for entry in result.task_progress.events
        ] == ["evt_001", "evt_004", "evt_005"]
        assert result.task_progress.total_events == 3
        # workspace(evt_002)与 private(evt_003)不进统计。
        progress = result.current_snapshot["progress"]
        assert progress["completed_count"] == 1
        assert progress["active_count"] == 1
        assert progress["blocked_count"] == 0
        assert result.current_event is not None
        assert result.current_event["event_id"] == "evt_004"

    def test_executor_sees_public_and_workspace_events(self):
        _, service = _make_service()
        result = service.get_status(
            COLLET_ID,
            trunk_events=False,
            context=make_actor(EXECUTOR.agent_id),
        )
        assert [
            entry["event_id"] for entry in result.task_progress.events
        ] == ["evt_001", "evt_002", "evt_004", "evt_005"]
        # evt_002 可见但 Message 不计数;evt_003(private)不可见。
        assert result.current_snapshot["progress"]["completed_count"] == 1

    def test_filtered_event_percent_does_not_drive_percent(self):
        """不可见事件携带 percent 时不得驱动该 actor 的进度百分比。"""
        events = _fixture_events()
        events[4] = _event(
            "evt_005",
            event_kind="task.progress.updated",
            primitive_type="Activity",
            parent_event="evt_004",
            visibility="private",
            payload={"summary": "完成设计并准备提交 PR", "percent": 90},
        )
        _, service = _make_service(events=events)

        outsider = service.get_status(
            COLLET_ID, trunk_events=False, context=make_actor("u_9999")
        )
        # 外人看不到 private evt_005,percent 停留在可见的 evt_004(40)。
        assert outsider.task_progress.total_events == 2
        assert outsider.current_snapshot["progress"]["percent"] == 40

        principal = service.get_status(
            COLLET_ID, trunk_events=False, context=make_actor()
        )
        # principal 可见全量,percent 取最后携带值的事件(evt_005 的 90)。
        assert principal.current_snapshot["progress"]["percent"] == 90

    def test_invisible_current_event_is_none_but_pointer_kept(self):
        """current 事件本身不可见时投影为 None;collet 行指针不变。"""
        _, service = _make_service(collet=_collet(current_event_id="evt_002"))
        result = service.get_status(
            COLLET_ID, trunk_events=False, context=make_actor("u_9999")
        )
        assert result.current_event is None
        assert result.current_snapshot["progress"]["current_event_id"] == (
            "evt_002"
        )


class TestReadOnlyPath:
    """查询是纯读路径:不写库、不推进 context_version。"""

    def test_repeated_gets_never_mutate_aggregate(self):
        repo, service = _make_service()
        before = repo.collets[COLLET_ID]

        first = service.get_status(
            COLLET_ID, trunk_events=False, context=make_actor()
        )
        second = service.get_status(
            COLLET_ID, trunk_events=False, context=make_actor()
        )

        # 只触发了两次 get_current_status 读取;写路径零调用。
        assert repo.get_calls == [COLLET_ID, COLLET_ID]
        assert repo.write_calls == []
        # collet 行未变:版本、落库 progress 都保持原样。
        assert repo.collets[COLLET_ID] is before
        assert before.version == 4
        assert repo.snapshots[COLLET_ID].progress.percent == 0
        assert before.current_event_id == "evt_004"
        assert len(repo.events[COLLET_ID]) == 5
        # 两次只读结果一致,且未把投影值写回行内。
        assert first.current_snapshot == second.current_snapshot
        assert first.current_snapshot["progress"]["percent"] == 40
