import pytest

from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.repository.types import TaskNodeRecord
from agentclaw.community.core.repository.implementations.task.task_node_repository import (
    TaskNodeRepository,
)


def _node(node_id: str = "N-1", task_id: str = "T-1", status=Status.PENDING) -> TaskNodeRecord:
    return TaskNodeRecord(
        id=0,
        task_id=task_id,
        node_id=node_id,
        task_spec={"metadata": {"task_id": task_id, "title": "n", "instruction": "do"}},
        status=status,
    )


def test_insert_get_roundtrip(db):
    repo = TaskNodeRepository(db)
    stored = repo.insert(_node())
    assert stored.id > 0
    assert repo.get("T-1", "N-1") == stored


def test_update_status_truth(db):
    repo = TaskNodeRepository(db)
    repo.insert(_node("N-1", status=Status.PENDING))
    assert repo.update_status("T-1", "N-1", Status.RUNNING) is True
    assert repo.get("T-1", "N-1").status is Status.RUNNING
    assert repo.update_status("T-1", "missing", Status.DONE) is False


def test_list_nodes_and_by_status(db):
    repo = TaskNodeRepository(db)
    repo.insert(_node("N-1", status=Status.PENDING))
    repo.insert(_node("N-2", status=Status.RUNNING))
    repo.insert(_node("N-3", status=Status.PENDING))
    assert {n.node_id for n in repo.list_nodes("T-1")} == {"N-1", "N-2", "N-3"}
    assert {n.node_id for n in repo.list_by_status("T-1", Status.PENDING)} == {"N-1", "N-3"}


def test_list_by_status_unscoped(db):
    repo = TaskNodeRepository(db)
    repo.insert(_node("N-1", "T-1", Status.RUNNING))
    repo.insert(_node("N-2", "T-2", Status.RUNNING))
    assert len(repo.list_by_status(None, Status.RUNNING)) == 2


def test_logically_deleted_node_is_hidden_from_current_queries(db):
    repo = TaskNodeRepository(db)
    deleted = _node("N-deleted")
    deleted = TaskNodeRecord(
        id=deleted.id,
        task_id=deleted.task_id,
        node_id=deleted.node_id,
        task_spec=deleted.task_spec,
        status=deleted.status,
        is_deleted=True,
    )
    repo.insert(deleted)
    assert repo.get("T-1", "N-deleted") is None
    assert repo.list_nodes("T-1") == []
    assert repo.update_status("T-1", "N-deleted", Status.RUNNING) is False
