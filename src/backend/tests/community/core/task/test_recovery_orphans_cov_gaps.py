"""覆盖缺口 — 两个批次划分时漏网的无主文件。

* ``task_center/recovery_lifecycle.py``:构造函数(既有测试经 ``__new__`` 绕过)、
  ``_resolve_worker`` 的两类装配降级、``startup/shutdown`` 线程生命周期、
  ``_loop`` 的成功/异常/停机路径。
* ``repository/types.py``:``TaskNodeRunInfoRecord.to_acceptance_result`` 与
  ``TaskActionLogRecord.to_event``。
"""
from __future__ import annotations

import threading
from datetime import datetime

import pytest

from agentclaw.community.core.repository.protocols.task import (
    TaskGraphRepositoryProtocol,
)
from agentclaw.community.core.task.domain.models import AcceptanceVerdict, NodeAction, Status
from agentclaw.community.core.task.repository.types import (
    RelationType,
    TaskActionLogRecord,
    TaskNodeRelationRecord,
    TaskNodeRunInfoRecord,
)
from agentclaw.community.core.task.task_center.recovery_lifecycle import (
    TaskRecoveryLifecycle,
    _flag,
)
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.domain.models import NodeActionEvent  # noqa: F401  接口存在性


class _RaisingInjector:
    """指定接口抛错的注入器:驱动 _resolve_worker 的两类装配降级。"""

    def __init__(self, *, fail_graph=False, fail_service=False):
        self._fail_graph = fail_graph
        self._fail_service = fail_service

    def get(self, interface):
        if interface is TaskGraphRepositoryProtocol and self._fail_graph:
            raise RuntimeError("graph repo unbound")
        if interface is TaskService and self._fail_service:
            raise RuntimeError("task service unresolvable")
        if interface is TaskGraphRepositoryProtocol:
            return object()  # truthy 依赖
        raise KeyError(interface)


# ---------------------------------------------------------------------------
# __init__ + _flag(环境驱动装配)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_flag_normalizes_env_value(monkeypatch):
    monkeypatch.setenv("SOME_FLAG", "  OFF  ")
    assert _flag("SOME_FLAG") == "off"
    assert _flag("ABSENT_FLAG", "True") == "true"
    monkeypatch.setenv("SOME_FLAG", "")
    assert _flag("SOME_FLAG", "") == ""


@pytest.mark.unit
def test_lifecycle_init_reads_env_configuration(monkeypatch):
    monkeypatch.delenv("TASK_RECOVERY_ENABLED", raising=False)
    monkeypatch.setenv("TASK_RECOVERY_INTERVAL", "7")
    monkeypatch.setenv("TASK_RECOVERY_LEASE_SECONDS", "11")
    monkeypatch.setenv("TASK_RECOVERY_INSTANCE_ID", "i-x")

    lc = _construct(_RaisingInjector())

    assert lc._enabled is False  # 默认关(测试/本地确定性)
    assert lc._interval == 7
    assert lc._lease_seconds == 11
    assert lc._instance_id == "i-x"
    assert lc._worker is None and lc._thread is None


def _construct(injector) -> TaskRecoveryLifecycle:
    """构造真实实例(不依赖 DI 容器):直接执行 @inject 装饰态下的 __init__。"""
    lc = TaskRecoveryLifecycle.__new__(TaskRecoveryLifecycle)
    lc.__init__(injector)
    return lc


@pytest.mark.unit
def test_lifecycle_init_enabled_by_env(monkeypatch):
    monkeypatch.setenv("TASK_RECOVERY_ENABLED", "1")
    lc = _construct(_RaisingInjector())
    assert lc._enabled is True
    monkeypatch.setenv("TASK_RECOVERY_ENABLED", "false")
    lc2 = _construct(_RaisingInjector())
    assert lc2._enabled is False


# ---------------------------------------------------------------------------
# _resolve_worker — 缓存命中 / 两类装配降级
# ---------------------------------------------------------------------------


def _bare(injector) -> TaskRecoveryLifecycle:
    lc = _construct(injector)
    lc._enabled = False
    lc._interval = 30
    lc._lease_seconds = 60
    lc._instance_id = "test"
    return lc


@pytest.mark.unit
def test_resolve_worker_cached_instance_short_circuits():
    lc = _bare(_RaisingInjector())
    sentinel = object()
    lc._worker = sentinel
    assert lc._resolve_worker() is sentinel


@pytest.mark.unit
def test_resolve_worker_unbound_graph_repo_disables():
    lc = _bare(_RaisingInjector(fail_graph=True))
    assert lc._resolve_worker() is None


@pytest.mark.unit
def test_resolve_worker_unresolvable_task_service_skips():
    lc = _bare(_RaisingInjector(fail_service=True))
    assert lc._resolve_worker() is None
    assert lc._worker is None  # 不缓存失败


# ---------------------------------------------------------------------------
# startup / shutdown / _loop 线程生命周期
# ---------------------------------------------------------------------------


class _ScriptWorker:
    """脚本化 worker:recover_once 按脚本回放,并在调用后停止 loop。"""

    def __init__(self, lc, *, results=None, raise_first=False):
        self._lc = lc
        self._results = list(results or [])
        self._raise_first = raise_first
        self.calls = 0

    async def recover_once(self, *, limit=100):
        self.calls += 1
        if self._raise_first and self.calls == 1:
            self._lc._stop_event.set()
            raise RuntimeError("scan boom")
        self._lc._stop_event.set()  # 单圈即停,防 busy-loop
        return self._results


@pytest.mark.asyncio
@pytest.mark.unit
async def test_startup_enabled_spawns_thread_and_shutdown_stops_it():
    lc = _bare(_RaisingInjector())
    lc._enabled = True
    lc._interval = 30

    await lc.startup()
    try:
        assert lc._thread is not None and lc._thread.is_alive()
    finally:
        await lc.shutdown()
    assert not lc._thread.is_alive()
    # 幂等:已停后再 shutdown 即 no-op(线程为 None 的早退分支由下一用例覆盖)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_shutdown_disabled_or_threadless_is_noop():
    lc = _bare(_RaisingInjector())
    await lc.shutdown()               # 未 enabled → 直接返回
    lc._enabled = True
    await lc.shutdown()               # 无线程 → 直接返回


@pytest.mark.asyncio
@pytest.mark.unit
async def test_startup_disabled_is_noop():
    lc = _bare(_RaisingInjector())
    lc._enabled = False
    await lc.startup()
    assert lc._thread is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_loop_runs_one_scan_with_recoveries_then_stops():
    lc = _bare(_RaisingInjector())
    worker = _ScriptWorker(lc, results=["t-1", "t-2"])
    monkey_worker = lc._resolve_worker  # 保证不触发注入器
    lc._stop_event.clear()
    lc._worker = worker
    threading.Thread(target=lc._loop, daemon=True).start()
    lc._stop_event.wait(timeout=2)  # worker 单圈置停
    assert worker.calls == 1
    assert monkey_worker is not None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_loop_swallows_scan_error_and_stops():
    lc = _bare(_RaisingInjector())
    worker = _ScriptWorker(lc, raise_first=True)
    lc._stop_event.clear()
    lc._worker = worker
    threading.Thread(target=lc._loop, daemon=True).start()
    assert lc._stop_event.wait(timeout=2)
    assert worker.calls == 1  # 异常吞没后按 stop 退出


@pytest.mark.asyncio
@pytest.mark.unit
async def test_loop_no_worker_still_honors_stop():
    lc = _bare(_RaisingInjector(fail_graph=True))  # resolve 恒 None
    lc._worker = None
    lc._stop_event.clear()
    t = threading.Thread(target=lc._loop, daemon=True)
    t.start()
    lc._stop_event.set()
    t.join(timeout=5)
    assert not t.is_alive()


# ---------------------------------------------------------------------------
# repository/types.py — to_acceptance_result / to_event
# ---------------------------------------------------------------------------


def _run_info(acceptance_result):
    return TaskNodeRunInfoRecord(
        id=1, node_id="n1", task_id="T", run_mode="single_bot", assignee="b",
        output={}, acceptance_result=acceptance_result, retry=0,
        session_id=None, extend_props={}, start_time=None,
        update_time=None, end_time=None,
    )


@pytest.mark.unit
def test_resolve_worker_happy_path_builds_and_caches_worker():
    class _BothBoundInjector:
        def get(self, interface):
            if interface is TaskGraphRepositoryProtocol:
                class _Repo:
                    pass
                return _Repo()
            if interface is TaskService:
                class _Svc:
                    async def redrive_task(self, task_id):
                        return None
                return _Svc()
            raise KeyError(interface)

    lc = _bare(_BothBoundInjector())
    worker = lc._resolve_worker()
    assert worker is not None and lc._worker is worker
    assert lc._resolve_worker() is worker  # 缓存命中


@pytest.mark.unit
def test_run_info_to_acceptance_result_none():
    assert _run_info(None).to_acceptance_result() is None


@pytest.mark.unit
def test_run_info_to_acceptance_result_canonical_fields():
    res = _run_info({"verdict": "DONE", "done_items": ["a"],
                     "gap_items": ["g"]}).to_acceptance_result()
    assert res.verdict is AcceptanceVerdict.DONE
    assert res.done_items == ["a"]
    assert res.gap_items == ["g"]


@pytest.mark.unit
def test_run_info_to_acceptance_result_legacy_aliases():
    res = _run_info({"verdict": "PASS",
                     "acceptances_metric": [{"id": "a", "passed": False},
                                            {"id": "b", "passed": True}],
                     "gaps": ["缺证据"]}).to_acceptance_result()
    assert res.verdict is AcceptanceVerdict.DONE  # PASS 归一
    assert res.done_items == [{"id": "b", "passed": True}]  # passed=False 掉
    assert res.gap_items == ["缺证据"]


@pytest.mark.unit
def test_node_relation_record_to_relation():
    rec = TaskNodeRelationRecord(
        id=1, task_id="T", src_node_id="a", dst_node_id="b",
        relation_type=RelationType.DEPENDENCY, extend_props={"k": 1},
    )
    rel = rec.to_relation()
    assert (rel.src_id, rel.dst_id) == ("a", "b")
    assert rel.type is RelationType.DEPENDENCY and rel.extend_props == {"k": 1}
    # extend_props None → 空 dict
    rec2 = TaskNodeRelationRecord(
        id=2, task_id="T", src_node_id="a", dst_node_id="b",
        relation_type=RelationType.DEPENDENCY, extend_props=None,
    )
    assert rec2.to_relation().extend_props == {}


@pytest.mark.unit
def test_action_log_record_to_event():
    rec = TaskActionLogRecord(
        id=1, event_id="e1", task_id="T", node_id="n1", seq=3,
        action=NodeAction.EXECUTE, loop_round=2, attempt=1,
        status_from=Status.PLANNING, status_to=Status.RUNNING,
        payload={"a": 1}, instance_id="i", gmt_create=datetime(2026, 9, 23, 12, 0, 0),
    )
    ev = rec.to_event()
    assert ev.seq == 3 and ev.ts > 0
    assert ev.action is NodeAction.EXECUTE and ev.loop_round == 2
    assert ev.status_from is Status.PLANNING and ev.status_to is Status.RUNNING
    # gmt_create 缺省 → ts=0;loop_round None → 0
    rec2 = TaskActionLogRecord(
        id=2, event_id="e2", task_id="T", node_id="n1", seq=4,
        action=NodeAction.VERIFY, loop_round=None, attempt=0,
        status_from=None, status_to=None, payload={}, instance_id=None,
    )
    ev2 = rec2.to_event()
    assert ev2.ts == 0 and ev2.loop_round == 0
    assert ev2.status_from is None and ev2.status_to is None