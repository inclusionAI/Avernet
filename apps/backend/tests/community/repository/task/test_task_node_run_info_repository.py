from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.task.repository.models import TaskNodeRunInfoModel
from agentclaw.community.core.task.repository.types import (
    TaskNodeRunInfoRecord,
    TaskNodeRunInfoUpdate,
)
from agentclaw.community.core.repository.implementations.task.task_node_run_info_repository import (
    TaskNodeRunInfoRepository,
)


def _run(task_id="T-1", node_id="N-1", retry=0, **kw) -> TaskNodeRunInfoRecord:
    base = dict(
        id=0, node_id=node_id, task_id=task_id, run_mode="single_bot",
        assignee="B-1", output=None, acceptance_result=None, retry=retry,
        session_id=None, extend_props=None, start_time=1000, update_time=None,
        end_time=None,
    )
    base.update(kw)
    return TaskNodeRunInfoRecord(**base)


def test_insert_get_by_retry(db):
    repo = TaskNodeRunInfoRepository(db)
    stored = repo.insert(_run(retry=0))
    assert stored.id > 0
    assert repo.get_by_retry("T-1", "N-1", 0) == stored


def test_duplicate_triple_raises(db):
    repo = TaskNodeRunInfoRepository(db)
    repo.insert(_run(retry=0))
    with pytest.raises(IntegrityError):
        repo.insert(_run(retry=0))
    # same node, different retry is allowed (1:N).
    repo.insert(_run(retry=1))


def test_get_latest_is_max_retry(db):
    repo = TaskNodeRunInfoRepository(db)
    repo.insert(_run(retry=0, start_time=1))
    repo.insert(_run(retry=2, start_time=2))
    repo.insert(_run(retry=1, start_time=3))
    latest = repo.get_latest("T-1", "N-1")
    assert latest is not None
    assert latest.retry == 2
    assert repo.get_latest("T-1", "missing") is None


def test_list_by_session_id_returns_all_rows_newest_first(db):
    repo = TaskNodeRunInfoRepository(db)
    first = repo.insert(_run(task_id="T-1", node_id="N-1", retry=2, session_id="S-1"))
    second = repo.insert(_run(task_id="T-2", node_id="N-2", retry=0, session_id="S-1"))
    repo.insert(_run(task_id="T-3", node_id="N-3", retry=0, session_id="S-2"))

    rows = repo.list_by_session_id("S-1")

    assert [row.task_id for row in rows] == [second.task_id, first.task_id]
    assert [row.retry for row in rows] == [second.retry, first.retry]
    assert repo.list_by_session_id("missing") == []


def test_list_by_session_id_compiles_portably_for_mysql():
    session = MagicMock()
    database = MagicMock()
    database.orm_session.return_value.__enter__.return_value = session
    query = session.query.return_value
    query.filter.return_value.order_by.return_value.all.return_value = []
    repository = TaskNodeRunInfoRepository(database)

    rows = repository.list_by_session_id("S-1")

    assert rows == []
    session.query.assert_called_once_with(TaskNodeRunInfoModel)
    order_args = query.filter.return_value.order_by.call_args.args
    statement = select(TaskNodeRunInfoModel).order_by(*order_args)
    sql = " ".join(str(statement.compile(dialect=mysql.dialect())).upper().split())
    assert "NULLS LAST" not in sql
    assert "GMT_MODIFIED DESC" in sql
    assert "ID DESC" in sql


def test_update_applies_only_non_none_fields(db):
    repo = TaskNodeRunInfoRepository(db)
    repo.insert(_run(retry=0, run_mode="single_bot", assignee="B-1", output=None))
    changed = repo.update(
        "T-1", "N-1", 0,
        TaskNodeRunInfoUpdate(run_mode="coop_group", output={"k": "v"}),
    )
    assert changed is True
    row = repo.get_by_retry("T-1", "N-1", 0)
    assert row.run_mode == "coop_group"
    assert row.output == {"k": "v"}
    assert row.assignee == "B-1"  # untouched
    assert row.update_time is not None
    # no-op patch does not touch the row.
    assert repo.update("T-1", "N-1", 0, TaskNodeRunInfoUpdate()) is False
    assert repo.update("T-1", "missing", 0, TaskNodeRunInfoUpdate(run_mode="x")) is False


def test_list_by_assignee_and_run_mode(db):
    repo = TaskNodeRunInfoRepository(db)
    repo.insert(_run(node_id="N-1", assignee="B-1", run_mode="single_bot", start_time=10))
    repo.insert(_run(node_id="N-2", assignee="B-2", run_mode="coop_group", start_time=20))
    assert {r.node_id for r in repo.list_by_assignee("B-1")} == {"N-1"}
    assert {r.node_id for r in repo.list_by_run_mode("coop_group")} == {"N-2"}
    assert {r.node_id for r in repo.list_by_run_mode("single_bot", start_time_since=15)} == set()