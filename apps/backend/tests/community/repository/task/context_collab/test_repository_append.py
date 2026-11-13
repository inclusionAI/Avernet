"""``ContextCollabRepository.append_event`` 落库行为(spec §9.4 规则 2/4/5)。

覆盖(追加不设幂等键,spec 作者裁决;顺序即自增 id,主干在投影层推导):
``expected_version`` 乐观并发冲突不落任何半态、逐字重发被 CAS 拒绝、
``parent_event`` 存在性校验与 CAS 版本推进。以真实落库行为为准校验。
"""

import dataclasses
import json
from datetime import datetime

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
from tests.community.core.task.context_collab.product_catalog import PRODUCT_CATALOG

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
            ResourceRef(
                resource_type="forum_topic", resource_id="topic_001", revision="3"
            ),
        ),
        product_kind="team_todo",
        workflow_kind="team_todo_execution",
    )


def _created_context(repo: ContextCollabRepository) -> str:
    record = repo.create_context(
        CreateContextRecordRequest(
            context_spec=_context_spec(),
            initial_event=InitialEventDraft(
                event_kind="task.created",
                primitive_type="Task",
                actor_ref=CREATOR,
                payload={"objective": OBJECTIVE},
            ),
        )
    )
    return record.collet.collet_id


def _append_request(
    expected_version: int,
    parent_event: str | None,
    *,
    snapshot_updater=None,
) -> AppendEventRecordRequest:
    return AppendEventRecordRequest(
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
        .order_by(ContextCollabEventModel.id.asc())
        .all()
    )


class TestAppendEvent:
    def test_happy_path_updates_snapshot_and_version(self, db):
        repo = ContextCollabRepository(db, product_catalog=PRODUCT_CATALOG)
        context_id = _created_context(repo)
        initial_status = repo.get_current_status(context_id)
        parent = initial_status.events[0].event_id

        record = repo.append_event(
            context_id,
            _append_request(expected_version=1, parent_event=parent),
        )

        assert isinstance(record, EventAppendedRecord)
        assert record.event.event_id.startswith("evt_")
        assert record.event.parent_event == parent
        assert record.event.context_version_after == 2
        assert isinstance(record.event.occurred_at, datetime)
        assert record.collet.version == 2
        assert record.collet.current_event_id == record.event.event_id
        assert record.current_snapshot.status == "in_progress"

        with db.orm_session() as session:
            row = _collet_row(session, context_id)
            assert row.context_version == 2
            assert row.current_event_id == record.event.event_id
            assert json.loads(row.current_snapshot)["status"] == "in_progress"
            events = _event_rows(session, context_id)
            # 顺序即插入顺序:2 行,按 id 升序初始事件在前。
            assert len(events) == 2
            assert events[1].event_id == record.event.event_id
            assert events[1].context_version_after == 2
            assert json.loads(events[1].payload) == MESSAGE_PAYLOAD
            assert isinstance(events[1].occurred_at, datetime)
            assert (
                record.event.occurred_at.replace(tzinfo=None) == events[1].occurred_at
            )

    def test_verbatim_resubmit_rejected_by_cas_no_new_rows(self, db):
        """追加无幂等键(Ruling R8):逐字重发被 expected_version 拒绝,
        不落第二条事件(乐观并发即事实上的防重),版本不 bump。"""
        repo = ContextCollabRepository(db, product_catalog=PRODUCT_CATALOG)
        context_id = _created_context(repo)
        parent = repo.get_current_status(context_id).events[0].event_id

        first = repo.append_event(
            context_id,
            _append_request(expected_version=1, parent_event=parent),
        )
        assert repo.get_current_status(context_id).collet.version == 2

        with pytest.raises(ContextCollabVersionConflict):
            repo.append_event(
                context_id,
                _append_request(expected_version=1, parent_event=parent),
            )

        with db.orm_session() as session:
            events = _event_rows(session, context_id)
            assert [e.event_id for e in events] == [
                parent,
                first.event.event_id,
            ]
            assert _collet_row(session, context_id).context_version == 2

    def test_stale_expected_version_conflicts_without_writing(self, db):
        repo = ContextCollabRepository(db, product_catalog=PRODUCT_CATALOG)
        context_id = _created_context(repo)
        parent = repo.get_current_status(context_id).events[0].event_id
        appended = repo.append_event(
            context_id,
            _append_request(expected_version=1, parent_event=parent),
        )
        assert appended.collet.version == 2

        with pytest.raises(ContextCollabVersionConflict):
            repo.append_event(
                context_id,
                _append_request(expected_version=1, parent_event=parent),
            )

        with db.orm_session() as session:
            row = _collet_row(session, context_id)
            assert row.context_version == 2
            assert row.current_event_id == appended.event.event_id
            assert json.loads(row.current_snapshot)["status"] == "in_progress"
            assert len(_event_rows(session, context_id)) == 2

    def test_parent_older_than_head_appends_branch_event(self, db):
        repo = ContextCollabRepository(db, product_catalog=PRODUCT_CATALOG)
        context_id = _created_context(repo)
        initial = repo.get_current_status(context_id)
        root_event_id = initial.events[0].event_id

        # 第一次:接在初始事件后(head 推进)。
        head = repo.append_event(
            context_id,
            _append_request(expected_version=1, parent_event=root_event_id),
        )
        assert head.collet.current_event_id == head.event.event_id

        # 第二次:parent_event 指向链中旧事件(root ≠ 当前链头 head)→ 分支;
        # snapshot_updater 收到的是持锁读出的已落库快照,不是内存旧值。
        seen: list[CurrentSnapshot] = []

        def branch_updater(current: CurrentSnapshot) -> CurrentSnapshot:
            seen.append(current)
            return dataclasses.replace(current, status="reviewing")

        branch = repo.append_event(
            context_id,
            _append_request(
                expected_version=2,
                parent_event=root_event_id,
                snapshot_updater=branch_updater,
            ),
        )

        assert branch.event.event_id != head.event.event_id
        assert branch.event.context_version_after == 3
        assert branch.collet.version == 3
        assert [snap.status for snap in seen] == ["in_progress"]

        with db.orm_session() as session:
            events = _event_rows(session, context_id)
            # 三行按 id 升序:初始、head、branch(分支挂在 root 上)。
            assert [e.parent_event for e in events] == [
                None,
                root_event_id,
                root_event_id,
            ]
            assert (
                json.loads(_collet_row(session, context_id).current_snapshot)["status"]
                == "reviewing"
            )

    def test_unknown_parent_event_is_rejected_without_writes(self, db):
        repo = ContextCollabRepository(db, product_catalog=PRODUCT_CATALOG)
        context_id = _created_context(repo)

        with pytest.raises(ContextCollabParentEventError):
            repo.append_event(
                context_id,
                _append_request(
                    expected_version=1,
                    parent_event="evt_not_exist",
                ),
            )

        with db.orm_session() as session:
            events = _event_rows(session, context_id)
            assert len(events) == 1
            assert _collet_row(session, context_id).context_version == 1

    def test_none_parent_appends_new_trunk_event(self, db):
        repo = ContextCollabRepository(db, product_catalog=PRODUCT_CATALOG)
        context_id = _created_context(repo)

        record = repo.append_event(
            context_id,
            _append_request(expected_version=1, parent_event=None),
        )

        assert record.event.parent_event is None
        status = repo.get_current_status(context_id)
        assert record.event.event_id in [e.event_id for e in status.events]

        with db.orm_session() as session:
            events = _event_rows(session, context_id)
            assert len(events) == 2
            assert events[1].event_id == record.event.event_id
            assert events[1].parent_event is None

    def test_unknown_collab_context_id_raises_not_found(self, db):
        repo = ContextCollabRepository(db, product_catalog=PRODUCT_CATALOG)

        with pytest.raises(ContextCollabNotFoundError):
            repo.append_event(
                "collet_unknown",
                _append_request(expected_version=1, parent_event=None),
            )

        with db.orm_session() as session:
            assert session.query(ContextCollabEventModel).count() == 0
