from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.repository.types import TaskInfoRecord
from agentclaw.community.core.repository.implementations.task.task_info_repository import (
    TaskInfoRepository,
)


def _record(task_id: str = "T-1", status: Status = Status.PENDING) -> TaskInfoRecord:
    return TaskInfoRecord(
        id=0,
        task_id=task_id,
        source_type="bot",
        owner_user_id="U-1",
        owner_bot_id="B-1",
        execution_config={"max_depth": 3},
        task_spec={"metadata": {"task_id": task_id, "title": "t", "instruction": "do"}},
        status=status,
    )


def test_insert_then_get_roundtrips(db):
    repo = TaskInfoRepository(db)
    stored = repo.insert(_record())
    assert stored.id > 0
    assert stored.task_id == "T-1"
    assert stored.status is Status.PENDING
    assert stored.task_spec["metadata"]["title"] == "t"
    assert stored.gmt_create is not None

    again = repo.get("T-1")
    assert again == stored


def test_duplicate_task_id_raises(db):
    repo = TaskInfoRepository(db)
    repo.insert(_record("T-1"))
    with pytest.raises(IntegrityError):
        repo.insert(_record("T-1"))


def test_update_status_returns_rowcount_truth(db):
    repo = TaskInfoRepository(db)
    repo.insert(_record("T-1", Status.PENDING))
    assert repo.update_status("T-1", Status.RUNNING) is True
    assert repo.get("T-1").status is Status.RUNNING
    assert repo.update_status("missing", Status.RUNNING) is False


def test_list_by_status(db):
    repo = TaskInfoRepository(db)
    repo.insert(_record("T-1", Status.PENDING))
    repo.insert(_record("T-2", Status.RUNNING))
    repo.insert(_record("T-3", Status.PENDING))
    pending = repo.list_by_status(Status.PENDING)
    assert {r.task_id for r in pending} == {"T-1", "T-3"}
    assert repo.list_by_status(Status.DONE) == []
