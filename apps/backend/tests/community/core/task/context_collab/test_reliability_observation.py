"""G2 可靠性规则与 G3 查询投影纯函数单测(Task 6)。

覆盖 ``compute_is_trunk`` 主干判定真值表、``ensure_expected_version``
乐观并发校验、``project_events`` 的主干筛选与状态推导(§12.2 #4:
``trunk_events`` 只筛主干,不改变事件 status 与顺序),以及
``build_progress`` 的非 Message 计数与 percent 规则(spec §6.4)。
不触碰数据库,纯内存纯函数。
"""

from datetime import datetime

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g2_keep_reliable_live.reliability import (
    compute_is_trunk,
    ensure_expected_version,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.observation import (
    EventProjection,
    build_progress,
    project_events,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)

OCCURRED_AT = datetime(2026, 11, 10, 8, 30, 0)


def _record(event_id, sequence_no, parent_event, *, is_trunk=True):
    return EventRecord(
        collab_context_id="collet_todo_001",
        event_id=event_id,
        request_id=f"req_{event_id}",
        sequence_no=sequence_no,
        parent_event=parent_event,
        is_trunk=is_trunk,
        event_kind="task.progress.updated",
        primitive_type="Task",
        actor_ref=PrincipalRef(principal_type="human", principal_id="u_1001"),
        visibility="workspace",
        payload={"summary": f"{event_id} summary"},
        context_version_after=sequence_no,
        occurred_at=OCCURRED_AT,
    )


def _chain_records():
    """evt_001..evt_005 主干链 + 一条 is_trunk=False 的分支事件 evt_002b。"""
    return [
        _record("evt_001", 1, None),
        _record("evt_002", 2, "evt_001"),
        _record("evt_003", 3, "evt_002"),
        _record("evt_004", 4, "evt_003"),
        _record("evt_005", 5, "evt_004"),
        _record("evt_002b", 6, "evt_002", is_trunk=False),
    ]


def _projection(event_id, status, primitive_type="Task"):
    return EventProjection(
        event_id=event_id,
        parent_event=None,
        event_kind="task.progress.updated",
        primitive_type=primitive_type,
        status=status,
        summary=None,
        content=None,
        occurred_at=OCCURRED_AT,
    )


def test_compute_is_trunk_truth_table():
    assert compute_is_trunk(None, "evt_001") is True
    assert compute_is_trunk("evt_001", "evt_001") is True
    assert compute_is_trunk("evt_001", "evt_003") is False


def test_ensure_expected_version_conflict_carries_current_version():
    ensure_expected_version(1, 1)

    with pytest.raises(ContextCollabVersionConflict) as excinfo:
        ensure_expected_version(1, 2)
    assert "current context_version 2" in str(excinfo.value)


def test_project_events_trunk_filter_keeps_status_and_order():
    records = _chain_records()
    full = project_events(records, current_event_id="evt_004", trunk_events=False)
    trunk = project_events(records, current_event_id="evt_004", trunk_events=True)

    assert [p.event_id for p in trunk] == [
        "evt_001",
        "evt_002",
        "evt_003",
        "evt_004",
        "evt_005",
    ]
    assert [p.event_id for p in full] == [
        "evt_001",
        "evt_002",
        "evt_003",
        "evt_004",
        "evt_005",
        "evt_002b",
    ]
    assert [p.status for p in trunk] == [
        "completed",
        "completed",
        "completed",
        "active",
        "pending",
    ]

    full_status = {p.event_id: p.status for p in full}
    for projection in trunk:
        assert full_status[projection.event_id] == projection.status


def test_build_progress_counts_and_percent_fallback():
    projections = [
        _projection("evt_001", "completed"),
        _projection("evt_002", "completed"),
        _projection("evt_003", "completed"),
        _projection("evt_004", "active"),
        _projection("evt_005", "pending"),
    ]
    snapshot = build_progress(
        projections, current_event_id="evt_004", last_percent=None
    )

    assert snapshot.percent == 75
    assert snapshot.completed_count == 3
    assert snapshot.active_count == 1
    assert snapshot.blocked_count == 0
    assert snapshot.current_event_id == "evt_004"


def test_build_progress_last_percent_wins_and_current_event_passthrough():
    projections = [
        _projection("evt_001", "completed"),
        _projection("evt_002", "completed"),
        _projection("evt_003", "completed"),
        _projection("evt_004", "active"),
        _projection("evt_005", "pending"),
    ]
    snapshot = build_progress(
        projections, current_event_id="evt_003", last_percent=40
    )

    assert snapshot.percent == 40
    assert snapshot.current_event_id == "evt_003"


def test_build_progress_ignores_message_events():
    projections = [
        _projection("evt_001", "completed"),
        _projection("evt_002", "completed"),
        _projection("evt_003", "completed"),
        _projection("evt_004", "active"),
        _projection("evt_005", "pending"),
        _projection("evt_002m", "completed", primitive_type="Message"),
    ]
    snapshot = build_progress(
        projections, current_event_id="evt_004", last_percent=None
    )

    assert snapshot.completed_count == 3
