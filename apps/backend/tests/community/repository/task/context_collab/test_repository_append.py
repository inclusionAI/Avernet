"""``ContextCollabRepository.append_event`` 落库行为(spec §9.4 规则 2/4/5)。

覆盖:持锁事务内按 ``(collab_context_id, request_id)`` 幂等重放(重放先于
版本检查、不写入不 bump)、``expected_version`` 乐观并发冲突不落任何半态、
``parent_event`` 存在性校验、``sequence_no`` 事务内分配、``is_trunk``
主干判定与 CAS 版本推进。幂等与冲突均以真实落库行为校验,不只看返回对象。
"""

import dataclasses
import json
from datetime import datetime
from uuid import uuid4

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabParentEventError,
    ContextCollabVersionConflict,
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
    ContextSpec,
    CurrentSnapshot,
)
from agentclaw.community.core.task.context_collab.persistence.models import (
    ContextCollabColletModel,
    ContextCollabEventModel,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    AppendEventRecordRequest,
    ContextCollabRepository,
    CreateContextRecordRequest,
    EventAppendedRecord,
    InitialEventDraft,
)

OBJECTIVE = "整理 Context 协作协议评审结论并提交可评审 PR"
EXECUTOR = AgentRef(agent_type="bot", agent_id="bot_coding_001")
CREATOR = PrincipalRef(principal_type="human", principal_id="u_1001")
MESSAGE_PAYLOAD = {
    "message_kind": "user_interaction",
    "content": "上下文做一下补充xxx",
}


def _context_spec() -> ContextSpec:
    return ContextSpec(
        space=SpaceRef(space_id="space_workspace_001", environment="prod"),
        principal_ref=CREATOR,
        method_kind="Single",
        executor_ref=EXECUTOR,
        objective=OBJECTIVE,
        resource_refs=(
            ResourceRef(resource_type="forum_topic", resource_id="topic_001", revision="3"),
        ),
        product_kind="team_todo",
        workflow_kind="team_todo_execution",
    )


def _created_context(repo: ContextCollabRepository) -> str:
    record = repo.create_context(
        CreateContextRecordRequest(
            create_request_id="create-" + uuid4().hex,
            context_spec=_context_spec(),
            initial_event=InitialEventDraft(
                event_kind="task.created",
                primitive_type="Task",
                actor_ref=CREATOR,
                payload={"objective": OBJECTIVE},
            ),
        )
    )
    return record.collet.collab_context_id


def _append_request(
    request_id: str,
    expected_version: int,
    parent_event: str | None,
    *,
    snapshot_updater=None,
) -> AppendEventRecordRequest:
    return AppendEventRecordRequest(
        request_id=request_id,
        expected_version=expected_version,
        event_kind="user_interaction.posted",
        primitive_type="Message",
        actor_ref=CREATOR,
        parent_event=parent_event,
        visibility="workspace",
        payload=dict(MESSAGE_PAYLOAD),
        snapshot_updater=(
            snapshot_updater
            if snapshot_updater is not None
            else (lambda s: dataclasses.replace(s, status="in_progress"))
        ),
    )


def _collet_row(db, collab_context_id: str) -> ContextCollabColletModel:
    return (
        db.query(ContextCollabColletModel)
        .filter(ContextCollabColletModel.collab_context_id == collab_context_id)
        .one()
    )


def _event_rows(db, collab_context_id: str) -> list[ContextCollabEventModel]:
    return (
        db.query(ContextCollabEventModel)
        .filter(ContextCollabEventModel.collab_context_id == collab_context_id)
        .order_by(ContextCollabEventModel.sequence_no.asc())
        .all()
    )


class TestAppendEvent:
    def test_happy_path_updates_snapshot_sequence_version_and_trunk(self, db):
        repo = ContextCollabRepository(db)
        context_id = _created_context(repo)
        initial_status = repo.get_current_status(context_id, trunk_events=False)
        parent = initial_status.events[0].event_id

        record = repo.append_event(
            context_id,
            _append_request("append-msg-001", expected_version=1, parent_event=parent),
        )

        assert isinstance(record, EventAppendedRecord)
        assert record.created is True
        assert record.event.event_id.startswith("evt_")
        assert record.event.sequence_no == 2
        assert record.event.parent_event == parent
        assert record.event.is_trunk is True  # parent == 锁行 current_event_id
        assert record.event.context_version_after == 2
        assert isinstance(record.event.occurred_at, datetime)
        assert record.collet.context_version == 2
        assert record.collet.current_event_id == record.event.event_id
        assert record.collet.current_snapshot.status == "in_progress"

        with db.orm_session() as session:
            row = _collet_row(session, context_id)
            assert row.context_version == 2
            assert row.current_event_id == record.event.event_id
            assert json.loads(row.current_snapshot)["status"] == "in_progress"
            events = _event_rows(session, context_id)
            assert [e.sequence_no for e in events] == [1, 2]
            assert events[1].request_id == "append-msg-001"
            assert events[1].is_trunk is True
            assert events[1].context_version_after == 2
            assert json.loads(events[1].payload) == MESSAGE_PAYLOAD
            assert isinstance(events[1].occurred_at, datetime)
            assert (
                record.event.occurred_at.replace(tzinfo=None) == events[1].occurred_at
            )

    def test_duplicate_request_id_replays_before_version_check(self, db):
        repo = ContextCollabRepository(db)
        context_id = _created_context(repo)
        parent = repo.get_current_status(context_id, trunk_events=False).events[0].event_id

        first = repo.append_event(
            context_id,
            _append_request("append-msg-002", expected_version=1, parent_event=parent),
        )
        first_version = repo.get_current_status(context_id, trunk_events=False).collet.context_version
        assert first_version == 2

        # 同 request_id 且 expected_version 已过期(1 != 2):重放优先于版本检查,
        # 直接返回原事件,不写任何行、不再 bump 版本。
        second = repo.append_event(
            context_id,
            _append_request("append-msg-002", expected_version=1, parent_event=parent),
        )

        assert second.created is False
        assert second.event.event_id == first.event.event_id
        assert second.event.context_version_after == first.event.context_version_after
        assert second.collet.context_version == 2
        assert second.collet.current_event_id == first.event.event_id
        with db.orm_session() as session:
            events = _event_rows(session, context_id)
            assert len(events) == 2
            assert _collet_row(session, context_id).context_version == 2

    def test_stale_expected_version_conflicts_without_writing(self, db):
        repo = ContextCollabRepository(db)
        context_id = _created_context(repo)
        parent = repo.get_current_status(context_id, trunk_events=False).events[0].event_id
        appended = repo.append_event(
            context_id,
            _append_request("append-msg-003", expected_version=1, parent_event=parent),
        )
        assert appended.collet.context_version == 2

        with pytest.raises(ContextCollabVersionConflict):
            repo.append_event(
                context_id,
                _append_request(
                    "append-msg-003-stale", expected_version=1, parent_event=parent
                ),
            )

        with db.orm_session() as session:
            row = _collet_row(session, context_id)
            assert row.context_version == 2
            assert row.current_event_id == appended.event.event_id
            assert json.loads(row.current_snapshot)["status"] == "in_progress"
            assert len(_event_rows(session, context_id)) == 2

    def test_parent_older_than_head_appends_branch_event(self, db):
        repo = ContextCollabRepository(db)
        context_id = _created_context(repo)
        initial = repo.get_current_status(context_id, trunk_events=False)
        root_event_id = initial.events[0].event_id

        # 第一次:接在链头 → 主干。
        head = repo.append_event(
            context_id,
            _append_request("append-chain-001", expected_version=1, parent_event=root_event_id),
        )
        assert head.event.is_trunk is True

        # 第二次:parent_event 指向链中旧事件(root ≠ 当前链头 head)→ 分支;
        # snapshot_updater 收到的是持锁读出的已落库快照,不是内存旧值。
        seen: list[CurrentSnapshot] = []

        def branch_updater(current: CurrentSnapshot) -> CurrentSnapshot:
            seen.append(current)
            return dataclasses.replace(current, status="reviewing")

        branch = repo.append_event(
            context_id,
            _append_request(
                "append-chain-002",
                expected_version=2,
                parent_event=root_event_id,
                snapshot_updater=branch_updater,
            ),
        )

        assert branch.event.is_trunk is False
        assert branch.event.sequence_no == 3
        assert branch.event.context_version_after == 3
        assert branch.collet.context_version == 3
        assert [snap.status for snap in seen] == ["in_progress"]

        with db.orm_session() as session:
            events = _event_rows(session, context_id)
            assert [e.is_trunk for e in events] == [True, True, False]
            assert json.loads(_collet_row(session, context_id).current_snapshot)[
                "status"
            ] == "reviewing"

    def test_unknown_parent_event_is_rejected_without_writes(self, db):
        repo = ContextCollabRepository(db)
        context_id = _created_context(repo)

        with pytest.raises(ContextCollabParentEventError):
            repo.append_event(
                context_id,
                _append_request(
                    "append-dangling-001",
                    expected_version=1,
                    parent_event="evt_not_exist",
                ),
            )

        with db.orm_session() as session:
            events = _event_rows(session, context_id)
            assert len(events) == 1
            assert _collet_row(session, context_id).context_version == 1

    def test_none_parent_appends_new_trunk_event(self, db):
        repo = ContextCollabRepository(db)
        context_id = _created_context(repo)

        record = repo.append_event(
            context_id,
            _append_request("append-trunk-001", expected_version=1, parent_event=None),
        )

        assert record.event.is_trunk is True
        assert record.event.parent_event is None
        assert record.event.sequence_no == 2

        trunk_only = repo.get_current_status(context_id, trunk_events=True)
        assert record.event.event_id in [e.event_id for e in trunk_only.events]

        with db.orm_session() as session:
            events = _event_rows(session, context_id)
            assert events[1].is_trunk is True
            assert events[1].parent_event is None

    def test_unknown_collab_context_id_raises_not_found(self, db):
        repo = ContextCollabRepository(db)

        with pytest.raises(ContextCollabNotFoundError):
            repo.append_event(
                "collet_unknown",
                _append_request("append-ghost-001", expected_version=1, parent_event=None),
            )

        with db.orm_session() as session:
            assert session.query(ContextCollabEventModel).count() == 0
