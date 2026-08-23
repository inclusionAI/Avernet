import pytest
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.task.repository.types import TaskCallbackRecord
from agentclaw.community.core.repository.implementations.task.task_callback_repository import (
    TaskCallbackRepository,
)


def _cb(run_id="R-1", node_id="N-1", session="S-1", **kw) -> TaskCallbackRecord:
    base = dict(
        id=0, invoker="bcs", run_id=run_id, node_id=node_id, main_session_id=session,
        status="completed", orig_callback_data='{"raw": 1}', execution_graph=None,
        result={"success": True}, result_success=True, exec_error=None, extend_props=None,
    )
    base.update(kw)
    return TaskCallbackRecord(**base)


def test_insert_get_roundtrip(db):
    repo = TaskCallbackRepository(db)
    stored = repo.insert(_cb())
    assert stored.id > 0
    assert repo.get("R-1", "N-1") == stored
    assert stored.result == {"success": True}
    assert stored.orig_callback_data == '{"raw": 1}'


def test_duplicate_run_node_raises(db):
    repo = TaskCallbackRepository(db)
    repo.insert(_cb(run_id="R-1", node_id="N-1"))
    with pytest.raises(IntegrityError):
        repo.insert(_cb(run_id="R-1", node_id="N-1"))
    # different node_id under same run_id is allowed.
    repo.insert(_cb(run_id="R-1", node_id="N-2"))


def test_list_by_session(db):
    repo = TaskCallbackRepository(db)
    repo.insert(_cb(run_id="R-1", node_id="N-1", session="S-1"))
    repo.insert(_cb(run_id="R-2", node_id="N-2", session="S-1"))
    repo.insert(_cb(run_id="R-3", node_id="N-3", session="S-2"))
    rows = repo.list_by_session("S-1")
    assert {r.run_id for r in rows} == {"R-1", "R-2"}
    assert repo.list_by_session("missing") == []


def test_upsert_inserts_then_refreshes_same_row(db):
    repo = TaskCallbackRepository(db)
    assert repo.get("R-1", "N-1") is None
    r1 = repo.upsert(_cb(run_id="R-1", node_id="N-1", status="running",
                         result_success=None, exec_error=None))
    assert r1.id > 0
    assert repo.get("R-1", "N-1").status == "running"
    # 回投可重放:start 后 result → 同 (run_id,node_id) 覆盖可变列,不撞唯一键
    r2 = repo.upsert(_cb(run_id="R-1", node_id="N-1", status="completed", result_success=True))
    stored = repo.get("R-1", "N-1")
    assert stored.status == "completed"
    assert stored.result_success is True
    # 仍是同一行(upsert 未新增)
    assert sum(1 for r in repo.list_by_session("S-1") if r.run_id == "R-1" and r.node_id == "N-1") == 1


def test_get_latest_by_session_returns_newest(db):
    repo = TaskCallbackRepository(db)
    repo.insert(_cb(run_id="R-1", node_id="N-1", session="S-1", execution_graph={"v": 1}))
    repo.insert(_cb(run_id="R-2", node_id="N-2", session="S-1", execution_graph={"v": 2}))
    repo.insert(_cb(run_id="R-3", node_id="N-3", session="S-2", execution_graph={"v": 9}))
    latest = repo.get_latest_by_session("S-1")
    assert latest is not None
    assert latest.execution_graph == {"v": 2}
    assert repo.get_latest_by_session("missing") is None
