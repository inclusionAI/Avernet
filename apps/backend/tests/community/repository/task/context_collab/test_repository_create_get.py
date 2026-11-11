"""``ContextCollabRepository`` create/read 落库行为(spec §6.2 / §9.4 规则 1/3/4)。

覆盖:创建同事务插 1 行 collet + 1 行初始 task.created 事件(创建
不设幂等键)、``get_current_status`` 的 trunk 过滤与 sequence_no 升序、
未知 ID 返回 None。JSON 编解码以落库行为准校验,不只看返回对象。
"""
import json
from datetime import datetime, timedelta, timezone

from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    ContextSpec,
)
from agentclaw.community.core.task.context_collab.persistence.models import (
    ContextCollabColletModel,
    ContextCollabEventModel,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    CreateContextRecordRequest,
    ContextCollabRepository,
    ContextCreatedRecord,
    ContextStatusRecord,
    InitialEventDraft,
)
from agentclaw.community.utils.env_utils import get_current_env

OBJECTIVE = "整理 Context 协作协议评审结论并提交可评审 PR"
EXECUTOR = AgentRef(agent_type="bot", agent_id="bot_coding_001")
CREATOR = PrincipalRef(principal_type="human", principal_id="u_1001")


def _context_spec(executor: AgentRef | None = EXECUTOR) -> ContextSpec:
    """spec §6.2 指定执行请求示例的创建参数。"""
    return ContextSpec(
        space=SpaceRef(space_id="space_workspace_001", environment="prod"),
        principal_ref=CREATOR,
        method_kind="Single",
        executor_ref=executor,
        objective=OBJECTIVE,
        resource_refs=(
            ResourceRef(resource_type="forum_topic", resource_id="topic_001", revision="3"),
        ),
        product_kind="team_todo",
        workflow_kind="team_todo_execution",
    )


def _create_request(
    executor: AgentRef | None = EXECUTOR,
) -> CreateContextRecordRequest:
    return CreateContextRecordRequest(
        context_spec=_context_spec(executor),
        initial_event=InitialEventDraft(
            event_kind="task.created",
            primitive_type="Task",
            actor_ref=CREATOR,
            payload={"objective": OBJECTIVE},
        ),
    )


class TestCreateContext:
    def test_inserts_one_collet_and_one_initial_event(self, db):
        repo = ContextCollabRepository(db)

        record = repo.create_context(_create_request())

        assert isinstance(record, ContextCreatedRecord)
        with db.orm_session() as session:
            collets = session.query(ContextCollabColletModel).all()
            events = session.query(ContextCollabEventModel).all()
            assert len(collets) == 1
            assert len(events) == 1

            row = collets[0]
            assert row.collab_context_id == record.collet.collab_context_id
            assert row.collab_context_id.startswith("collet_")
            assert row.context_version == 1
            assert row.current_event_id == record.initial_event.event_id
            assert row.env == get_current_env()
            assert json.loads(row.context_spec) == {
                "space": {"space_id": "space_workspace_001", "environment": "prod"},
                "principal_ref": {
                    "principal_type": "human",
                    "principal_id": "u_1001",
                },
                "method_kind": "Single",
                "executor_ref": {"agent_type": "bot", "agent_id": "bot_coding_001"},
                "objective": OBJECTIVE,
                "resource_refs": [
                    {
                        "resource_type": "forum_topic",
                        "resource_id": "topic_001",
                        "revision": "3",
                    }
                ],
                "product_kind": "team_todo",
                "workflow_kind": "team_todo_execution",
            }
            assert json.loads(row.current_snapshot) == {
                "objective": OBJECTIVE,
                "status": "created",
                "principal_ref": {
                    "principal_type": "human",
                    "principal_id": "u_1001",
                },
                "executor_ref": {"agent_type": "bot", "agent_id": "bot_coding_001"},
                "method_kind": "Single",
                "progress": {
                    "percent": 0,
                    "completed_count": 0,
                    "active_count": 0,
                    "blocked_count": 0,
                    "current_event_id": None,
                },
            }

            evt = events[0]
            assert evt.event_id == record.initial_event.event_id
            assert evt.event_id.startswith("evt_")
            assert evt.collab_context_id == record.collet.collab_context_id
            # 创建不设幂等键:初始事件的 request_id 取其自身 event_id。
            assert evt.request_id == evt.event_id
            assert evt.sequence_no == 1
            assert evt.parent_event is None
            assert evt.is_trunk is True
            assert evt.event_kind == "task.created"
            assert evt.primitive_type == "Task"
            assert json.loads(evt.actor_ref) == {
                "principal_type": "human",
                "principal_id": "u_1001",
            }
            assert evt.visibility == "public"
            assert json.loads(evt.payload) == {"objective": OBJECTIVE}
            assert evt.context_version_after == 1
            assert isinstance(evt.occurred_at, datetime)
            assert evt.env == get_current_env()

            # SQLite DATETIME 落库值不含时区;时刻与创建返回值一致。
            assert record.initial_event.occurred_at.replace(tzinfo=None) == (
                evt.occurred_at
            )

        # 读路径 round-trip 必须恢复 tz-aware UTC:本仓储只写 UTC,
        # naive 恢复值统一补 UTC,与创建返回值可直接比较、可跨路径排序。
        status = repo.get_current_status(
            record.collet.collab_context_id, trunk_events=False
        )
        assert status is not None
        round_tripped = status.events[0]
        assert round_tripped.occurred_at == record.initial_event.occurred_at
        assert round_tripped.occurred_at.tzinfo is not None
        assert round_tripped.occurred_at.utcoffset() == timedelta(0)
        # 返回的领域对象与落库行一致。
        assert record.collet.context_spec == _context_spec()
        assert record.collet.current_snapshot.status == "created"
        assert record.collet.current_snapshot.executor_ref == EXECUTOR
        assert record.collet.current_event_id == record.initial_event.event_id
        assert record.collet.context_version == 1
        assert record.initial_event.actor_ref == CREATOR
        assert record.initial_event.payload == {"objective": OBJECTIVE}

    def test_repeated_create_makes_a_second_context(self, db):
        """创建不设幂等键(spec 作者裁决):重复请求各建一个上下文。"""
        repo = ContextCollabRepository(db)

        first = repo.create_context(_create_request())
        second = repo.create_context(_create_request())

        assert first.collet.collab_context_id != second.collet.collab_context_id
        assert first.initial_event.event_id != second.initial_event.event_id
        with db.orm_session() as session:
            assert session.query(ContextCollabColletModel).count() == 2
            assert session.query(ContextCollabEventModel).count() == 2
        # 每次创建的 JSON 列均独立 round-trip 无损。
        assert second.collet.context_spec == first.collet.context_spec
        assert second.collet.current_snapshot == first.collet.current_snapshot
        assert second.collet.current_snapshot.executor_ref == EXECUTOR


class TestGetCurrentStatus:
    def test_unknown_id_returns_none(self, db):
        repo = ContextCollabRepository(db)

        assert repo.get_current_status("collet_unknown", trunk_events=False) is None
        assert repo.get_current_status("collet_unknown", trunk_events=True) is None

    def test_trunk_flag_filters_but_never_drops_trunk_events(self, db):
        repo = ContextCollabRepository(db)
        # 无执行 Agent 的创建请求:覆盖 executor_ref=None 的 JSON round-trip。
        created = repo.create_context(_create_request(executor=None))
        assert created.collet.context_spec.executor_ref is None
        assert created.collet.current_snapshot.executor_ref is None

        full = repo.get_current_status(
            created.collet.collab_context_id, trunk_events=False
        )
        assert isinstance(full, ContextStatusRecord)
        assert full.collet == created.collet
        assert [event.event_id for event in full.events] == [
            created.initial_event.event_id
        ]
        # 初始事件是主干,True/False 都包含。
        trunk = repo.get_current_status(
            created.collet.collab_context_id, trunk_events=True
        )
        assert [event.event_id for event in trunk.events] == [
            created.initial_event.event_id
        ]

        # 手动直插一条非主干派生事件(直接 ORM,不走仓储 append_event)。
        with db.orm_session() as session:
            session.add(
                ContextCollabEventModel(
                    collab_context_id=created.collet.collab_context_id,
                    event_id="evt_branch_manual",
                    request_id="append-branch-manual",
                    sequence_no=2,
                    parent_event=created.initial_event.event_id,
                    is_trunk=False,
                    event_kind="user_interaction.posted",
                    primitive_type="Message",
                    actor_ref=json.dumps(
                        {"principal_type": "human", "principal_id": "u_1001"}
                    ),
                    visibility="workspace",
                    payload=json.dumps(
                        {
                            "message_kind": "user_interaction",
                            "content": "上下文做一下补充xxx",
                        }
                    ),
                    context_version_after=2,
                    occurred_at=datetime.now(timezone.utc),
                )
            )

        full = repo.get_current_status(
            created.collet.collab_context_id, trunk_events=False
        )
        assert [event.sequence_no for event in full.events] == [1, 2]
        assert full.events[0].is_trunk is True
        assert full.events[1].is_trunk is False
        assert full.events[1].payload == {
            "message_kind": "user_interaction",
            "content": "上下文做一下补充xxx",
        }
        assert full.events[1].actor_ref == CREATOR
        assert full.events[1].parent_event == created.initial_event.event_id
        trunk = repo.get_current_status(
            created.collet.collab_context_id, trunk_events=True
        )
        assert [event.event_id for event in trunk.events] == [
            created.initial_event.event_id
        ]
