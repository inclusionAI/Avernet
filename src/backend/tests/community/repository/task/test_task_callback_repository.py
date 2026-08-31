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
    repo.upsert(_cb(run_id="R-1", node_id="N-1", status="completed", result_success=True))
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


def test_upsert_error_preserves_other_fields(db):
    """解析失败兜底:upsert_error 仅更新 exec_error + extend_props,保留既有行的其它字段。"""
    repo = TaskCallbackRepository(db)
    repo.insert(_cb(run_id="R-1", node_id="N-1", status="running",
                    result={"success": True}, result_success=True,
                    execution_graph={"g": 1}))
    # 兜底记录:exec_error + extend_props=原始 body;其它字段传 None(不应覆盖既有)
    repo.upsert_error(_cb(run_id="R-1", node_id="N-1", status=None, result=None,
                          result_success=None, execution_graph=None,
                          exec_error="bad-json", extend_props={"raw": 1}))
    stored = repo.get("R-1", "N-1")
    assert stored.exec_error == "bad-json"
    assert stored.extend_props == {"raw": 1}
    # 其它既有字段保留,未被 None 覆盖
    assert stored.status == "running"
    assert stored.result == {"success": True}
    assert stored.result_success is True
    assert stored.execution_graph == {"g": 1}


def test_upsert_error_inserts_when_absent(db):
    """行不存在 → upsert_error 插入兜底记录(exec_error + extend_props + 主键)。"""
    repo = TaskCallbackRepository(db)
    assert repo.get("R-1", "N-1") is None
    repo.upsert_error(_cb(run_id="R-1", node_id="N-1", exec_error="bad-json",
                          extend_props={"raw": 1}))
    stored = repo.get("R-1", "N-1")
    assert stored is not None
    assert stored.exec_error == "bad-json"
    assert stored.extend_props == {"raw": 1}
