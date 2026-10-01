from agentclaw.community.core.repository.implementations.task.task_action_log_repository import (
    TaskActionLogRepository,
)
from agentclaw.community.core.task.domain.models import NodeAction, Status
from agentclaw.community.core.task.repository.types import TaskActionLogRecord


def test_action_log_is_bounded_and_ordered(db):
    repo = TaskActionLogRepository(db)
    repo.append_many([
        TaskActionLogRecord(
            id=0,
            event_id="event-1",
            task_id="T-1",
            node_id="N-1",
            seq=1,
            action=NodeAction.PLAN,
            loop_round=0,
            attempt=0,
            status_from=Status.PENDING,
            status_to=Status.PLANNING,
            payload={"children": []},
            instance_id="pod-a",
        ),
        TaskActionLogRecord(
            id=0,
            event_id="event-2",
            task_id="T-1",
            node_id="N-1",
            seq=2,
            action=NodeAction.DISPATCH,
            loop_round=0,
            attempt=0,
            status_from=Status.PLANNING,
            status_to=Status.RUNNING,
            payload={"outcome": "HIT_SINGLE"},
            instance_id="pod-a",
        ),
    ])
    rows = repo.list_by_task("T-1", limit=1)
    assert len(rows) == 1
    assert rows[0].event_id == "event-1"
    assert rows[0].action is NodeAction.PLAN
