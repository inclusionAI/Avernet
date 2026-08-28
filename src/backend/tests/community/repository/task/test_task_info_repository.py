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


def test_list_records_returns_full_records_and_filters(db):
    repo = TaskInfoRepository(db)
    repo.insert(_record("T-1", Status.PENDING))
    repo.insert(_record("T-2", Status.RUNNING))

    all_records = repo.list_records()
    assert [record.task_id for record in all_records] == ["T-2", "T-1"]
    assert all_records[0].task_spec["metadata"]["instruction"] == "do"

    running = repo.list_records(Status.RUNNING)
    assert [record.task_id for record in running] == ["T-2"]


def test_list_records_filters_owner(db):
    repo = TaskInfoRepository(db)
    repo.insert(_record("T-1"))
    other = _record("T-2")
    repo.insert(TaskInfoRecord(
        id=other.id,
        task_id=other.task_id,
        source_type=other.source_type,
        owner_user_id="U-2",
        owner_bot_id=other.owner_bot_id,
        execution_config=other.execution_config,
        task_spec=other.task_spec,
        status=other.status,
    ))

    records = repo.list_records(owner_user_id="U-1")

    assert [record.task_id for record in records] == ["T-1"]


def test_list_records_page_filters_and_returns_total(db):
    repo = TaskInfoRepository(db)
    repo.insert(_record("T-1", Status.PENDING))
    repo.insert(_record("T-2", Status.RUNNING))
    third = _record("T-3", Status.RUNNING)
    repo.insert(TaskInfoRecord(
        id=third.id,
        task_id=third.task_id,
        source_type=third.source_type,
        owner_user_id="U-2",
        owner_bot_id=third.owner_bot_id,
        execution_config=third.execution_config,
        task_spec=third.task_spec,
        status=third.status,
    ))

    records, total = repo.list_records_page(
        Status.RUNNING,
        owner_user_id="U-1",
        page=1,
        page_size=1,
    )

    assert total == 1
    assert [record.task_id for record in records] == ["T-2"]
