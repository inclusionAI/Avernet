"""POST /api/v1/collaboration/tasks/callback/report 上 manager_worker CloudEvent 的当前契约测试。

当前 callback/report 对 manager-worker 事件只返回 200 ack，不落库、不触发任务收敛。"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from fastapi.testclient import TestClient
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.task.router import router as task_internal_router
from agentclaw.community.adapters.http.openapi_v1.task.router import router as task_router
from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.core.repository.protocols.task import (
    TaskCallbackRepositoryProtocol, TaskInfoRepositoryProtocol,
)


class _FakeCallbackRepo:
    """可累积的 fake:upsert 按 (run_id,node_id) 存、按 session 记 latest。"""

    def __init__(self):
        self.rows: dict[tuple, object] = {}
        self.latest_by_session: dict[str, object] = {}
        self.upserts: list = []

    def insert(self, rec):  # noqa: ANN001
        raise NotImplementedError

    def upsert(self, rec):  # noqa: ANN001
        self.upserts.append(rec)
        self.rows[(rec.run_id, rec.node_id or "")] = rec
        self.latest_by_session[rec.main_session_id] = rec
        return rec

    def get(self, run_id, node_id):  # noqa: ANN001
        return self.rows.get((run_id, node_id or ""))

    def list_by_session(self, main_session_id, *, limit=100):  # noqa: ANN001
        return [r for r in self.rows.values() if r.main_session_id == main_session_id]

    def get_latest_by_session(self, main_session_id):  # noqa: ANN001
        return self.latest_by_session.get(main_session_id)


class _StubModule(Module):
    @singleton
    @provider
    def discover(self) -> BotDiscoverServiceProtocol:
        class _D:
            def search_by_keyword(self, **kw):
                return {"total": 0, "items": []}
        return _D()  # type: ignore[return-value]

    @singleton
    @provider
    def bot_public(self) -> BotPublicServiceProtocol:
        class _B:
            def search_public_bots_by_keyword(self, **kw):
                return {"total": 0, "items": []}
        return _B()  # type: ignore[return-value]

    @provider
    def task_info_repo(self) -> TaskInfoRepositoryProtocol:
        return None  # type: ignore[return-value]

    @singleton
    @provider
    def callback_repo(self) -> TaskCallbackRepositoryProtocol:
        return _FakeCallbackRepo()


@pytest.fixture
def harness():
    from agentclaw.community.di.modules.task_module import TaskModule
    injector = Injector([TaskModule(), _StubModule()])
    fake = injector.get(TaskCallbackRepositoryProtocol)
    app = FastAPI()
    app.include_router(task_router)
    app.include_router(task_internal_router)
    attach_injector(app, injector)
    return TestClient(app), injector, fake


def _ce(event_type: str, scope: dict, data: dict | None = None, event_id: str | None = None) -> dict:
    return {
        "event_id": event_id or f"evt-{event_type}-{uuid.uuid4().hex[:4]}",
        "event_type": event_type,
        "source": "bcs",
        "scope": scope,
        "data": data or {},
    }


def test_manager_worker_cloud_events_are_acknowledged_without_persistence(harness):
    c, _inj, fake = harness
    sid = "sess-mw-1"
    events = [
        _ce("group.created", {"group_id": "g1", "session_id": sid}, {"status": "active"}),
        _ce("session.created", {"group_id": "g1", "session_id": sid}, {"status": "active"}),
        _ce("task.assigned", {"group_id": "g1", "session_id": sid, "task_id": "t1"},
             {"task_id": "t1", "manager_id": "m", "worker_id": "w"}),
        _ce("task.completed", {"group_id": "g1", "session_id": sid, "task_id": "t1"},
             {"task_id": "t1", "result": {"ok": 1}, "completed_at": "ts"}),
        _ce("session.completed", {"group_id": "g1", "session_id": sid},
             {"reason": "completed", "completed_by": "bcs-system", "summary": {"n": 1}}),
    ]
    for event in events:
        response = c.post("/api/v1/collaboration/tasks/callback/report", json=event)
        assert response.status_code == 200, response.text
    assert fake.upserts == []


def test_manager_worker_state_machine_event_not_diverted(harness):
    """state_machine.run.created 属 state_machine 链,不走 manager_worker 分流(fake 不被 upsert)。"""
    c, inj, fake = harness
    r = c.post("/api/v1/collaboration/tasks/callback/report",
               json=_ce("state_machine.run.created", {"group_id": "g1", "session_id": "s-sm", "run_id": "r1"},
                        {"run_id": "r1"}))
    assert r.status_code == 200, r.text
    # state_machine 事件经现有 translate_bcn → ingest 落 fake(workflow_source=bcn),未被分流到 manager_worker;
    # req2:run.created 映射为 Status.RUNNING(非 run.completed)
    assert any(rec.status == "RUNNING" for rec in fake.upserts)
    assert all(not getattr(rec, "invoker", "") == "bcn_manager_worker" for rec in fake.upserts)


def test_manager_worker_callback_status_events_are_acknowledged_without_persistence(harness):
    c, _inj, fake = harness
    for event_type in ("group.created", "session.created", "task.assigned", "task.completed", "session.completed"):
        scope = {"group_id": "g1", "session_id": f"sess-mw-status-{event_type}"}
        if event_type.startswith("task."):
            scope["task_id"] = "t1"
        response = c.post(
            "/api/v1/collaboration/tasks/callback/report",
            json=_ce(event_type, scope, {"reason": "completed"} if event_type == "session.completed" else {}),
        )
        assert response.status_code == 200, response.text
    assert fake.upserts == []
