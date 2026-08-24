"""manager_worker(BCN 任务协作群)CloudEvent 解析 + execution_graph 累积 merge 单测。

对齐语雀《BCS Group 回调接入说明》§4:manager_worker 事件(group.created/session.created/
task.assigned/task.completed/session.completed);message.created 框架不消费;子任务按 scope.task_id
upsert 进 tasks[];session.completed 是整协作终态(reason=completed|failed)。"""
from __future__ import annotations

from agentclaw.community.adapters.http.task.translator import (
    _BCN_MANAGER_WORKER_EVENTS,
    merge_manager_worker_execution_graph,
    parse_manager_worker_bcn,
)


def _ce(event_type: str, scope: dict | None = None, data: dict | None = None,
        event_id: str = "evt-1") -> dict:
    return {"event_id": event_id, "event_type": event_type, "source": "bcs",
            "scope": scope or {}, "data": data or {}}


# ===== parse =====
def test_parse_manager_worker_returns_fields_for_task_assigned():
    raw = _ce("task.assigned",
              scope={"group_id": "g1", "session_id": "s1", "task_id": "t1"},
              data={"task_id": "t1", "manager_id": "m", "worker_id": "w"})
    p = parse_manager_worker_bcn(raw)
    assert p == {
        "event_id": "evt-1", "event_type": "task.assigned",
        "group_id": "g1", "session_id": "s1", "task_id": "t1",
        "data": {"task_id": "t1", "manager_id": "m", "worker_id": "w"},
    }


def test_parse_returns_none_for_state_machine_or_message_or_unknown():
    assert parse_manager_worker_bcn(_ce("state_machine.run.created", scope={"run_id": "r1"})) is None
    assert parse_manager_worker_bcn(_ce("message.created", scope={"session_id": "s1"})) is None
    assert parse_manager_worker_bcn(_ce("unknown.thing")) is None


def test_manager_worker_event_set_exact():
    assert _BCN_MANAGER_WORKER_EVENTS == frozenset({
        "group.created", "session.created",
        "task.assigned", "task.completed",
        "session.completed",
    })


# ===== merge =====
def test_merge_group_created_initializes_state_with_empty_tasks():
    p = parse_manager_worker_bcn(_ce("group.created", scope={"session_id": "s1", "group_id": "g1"},
                                     data={"status": "active", "group_kind": "normal"}))
    st = merge_manager_worker_execution_graph(None, p)
    assert st["session_id"] == "s1"
    assert st["group_id"] == "g1"
    assert st["group_status"] == "active"
    assert st["tasks"] == []
    assert st["last_event_type"] == "group.created"


def test_merge_task_assigned_then_completed_upserts_same_task():
    base = merge_manager_worker_execution_graph(None,
        parse_manager_worker_bcn(_ce("group.created", scope={"session_id": "s1"}, data={})))
    st = merge_manager_worker_execution_graph(base,
        parse_manager_worker_bcn(_ce("task.assigned",
            scope={"session_id": "s1", "task_id": "t1"},
            data={"task_id": "t1", "worker_id": "w", "manager_id": "m", "assignment": {"q": 1}})))
    assert len(st["tasks"]) == 1
    assert st["tasks"][0]["task_id"] == "t1"
    assert st["tasks"][0]["status"] == "assigned"
    assert st["tasks"][0]["worker_id"] == "w"
    # 同 task_id 的 completed 覆盖状态、补 result/completed_at,不新增行
    st = merge_manager_worker_execution_graph(st,
        parse_manager_worker_bcn(_ce("task.completed",
            scope={"session_id": "s1", "task_id": "t1"},
            data={"task_id": "t1", "completed_at": "2026-08-18T10:01Z", "result": {"ok": 1}})))
    assert len(st["tasks"]) == 1
    assert st["tasks"][0]["status"] == "completed"
    assert st["tasks"][0]["result"] == {"ok": 1}
    assert st["tasks"][0]["completed_at"] == "2026-08-18T10:01Z"


def test_merge_different_tasks_kept_independently():
    base = merge_manager_worker_execution_graph(None,
        parse_manager_worker_bcn(_ce("task.assigned", scope={"session_id": "s1", "task_id": "t1"},
                                       data={"task_id": "t1"})))
    st = merge_manager_worker_execution_graph(base,
        parse_manager_worker_bcn(_ce("task.assigned", scope={"session_id": "s1", "task_id": "t2"},
                                       data={"task_id": "t2"})))
    assert {t["task_id"] for t in st["tasks"]} == {"t1", "t2"}


def test_merge_session_completed_sets_reason_and_summary():
    st = merge_manager_worker_execution_graph(None,
        parse_manager_worker_bcn(_ce("session.completed", scope={"session_id": "s1"},
            data={"reason": "completed", "completed_by": "bcs-system", "summary": {"n": 3}})))
    assert st["session_status"] == "completed"
    assert st["session_completed_by"] == "bcs-system"
    assert st["session_summary"] == {"n": 3}
    assert st["last_event_type"] == "session.completed"


def test_merge_reorder_tolerant_completed_first_then_assigned():
    """乱序:task.completed 先到(异常),随后 task.assigned 追到——upsert 不崩,最终该 task 仍存在。
    status 单调保护:已 completed 不被后到的 assigned 回退(乱序容忍的落地);assigned 的元数据照常补齐。"""
    p_completed = parse_manager_worker_bcn(_ce("task.completed", scope={"session_id": "s1", "task_id": "t1"},
                                                  data={"task_id": "t1", "result": {"ok": 1}}))
    st = merge_manager_worker_execution_graph(None, p_completed)
    assert st["tasks"][0]["status"] == "completed"
    p_assigned = parse_manager_worker_bcn(_ce("task.assigned", scope={"session_id": "s1", "task_id": "t1"},
                                                  data={"task_id": "t1", "worker_id": "w"}))
    st = merge_manager_worker_execution_graph(st, p_assigned)
    # 单条 upsert;status 不回退(仍 completed);后到 assigned 的 worker_id 照常补齐
    assert len(st["tasks"]) == 1
    assert st["tasks"][0]["status"] == "completed"
    assert st["tasks"][0]["worker_id"] == "w"
    assert st["tasks"][0]["result"] == {"ok": 1}
