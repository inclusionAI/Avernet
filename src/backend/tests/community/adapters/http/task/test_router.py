"""TDD for task HTTP adapter (Phase 0.6, re-wired 0.10 to Injected DI).

Phase 0.6 =骨架: schemas (pydantic) + router (APIRouter, Injected(TaskService)).
Handlers delegate to the TaskService Protocol; impl comes from the DI container.
Tests attach a custom injector holding a stub TaskService to a FastAPI app that
mounts only the task router, then drive endpoints via TestClient.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, singleton

from agentclaw.community.adapters.http.task.schemas import (
    ClarifyTaskRequest,
    CreateTaskRequest,
    EventReportRequest,
    TaskCreatedResponse,
    TaskDetailResponse,
    TaskProgressResponse,
)


# --- schemas ----------------------------------------------------------------

def test_create_task_request_required_title():
    req = CreateTaskRequest(title="fix PR")
    assert req.title == "fix PR"
    assert req.source == "api"
    assert req.background == ""


def test_create_task_request_rejects_empty_title():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CreateTaskRequest(title="")


def test_task_created_response_shape():
    r = TaskCreatedResponse(task_id="t1", status="drafting", seq=1)
    assert r.task_id == "t1"
    assert r.status == "drafting"
    assert r.seq == 1


def test_task_detail_response_minimal():
    r = TaskDetailResponse(task_id="t1", user_id="u1", status="drafting", loop_round=0)
    assert r.task_id == "t1"
    assert r.status == "drafting"
    assert r.nodes == []


def test_clarify_request_shape_with_confirmed_flag():
    a = ClarifyTaskRequest(patch={"goal": "x"})
    assert a.patch == {"goal": "x"}
    assert a.confirmed is False
    c = ClarifyTaskRequest(patch={}, confirmed=True)
    assert c.confirmed is True


def test_event_report_request_carries_kind_and_payload():
    e = EventReportRequest(kind="node.accepted", payload={"node_id": "n1"})
    assert e.kind == "node.accepted"
    assert e.payload["node_id"] == "n1"


def test_task_progress_response_shape():
    p = TaskProgressResponse(task_id="t1", status="executing", loop_round=2, done=3, total=5)
    assert p.done == 3 and p.total == 5


# --- router import + route table -------------------------------------------

def _make_app() -> FastAPI:
    from agentclaw.community.adapters.http.task.router import router
    app = FastAPI()
    app.include_router(router)
    return app


def test_router_imports_and_registers_routes():
    from agentclaw.community.adapters.http.task.router import router
    paths = {getattr(r, "path", None) for r in router.routes}
    assert "/api/tasks" in paths                      # GET list
    assert "/api/tasks/create" in paths                # POST create (n1 recognition)
    assert "/api/tasks/{task_id}" in paths            # GET detail
    assert "/api/tasks/{task_id}/clarify" in paths       # POST clarify(confirmed=True → DEFINED)
    assert "/api/tasks/{task_id}/progress" in paths    # GET progress
    assert "/api/tasks/{task_id}/events" in paths      # POST owner-bot 回投
    # --- canvas (secondary panel) endpoints (Phase 0.7, plan §7.2) ---
    assert "/api/tasks/{task_id}/graph" in paths                      # GET task graph
    assert "/api/tasks/{task_id}/nodes/{node_id}" in paths            # GET node detail
    assert "/api/tasks/{task_id}/nodes/{node_id}/sub-dag" in paths    # GET sub-dag drill-down
    assert "/api/tasks/{task_id}/graph/stream" in paths               # WS incremental


def test_router_tags():
    from agentclaw.community.adapters.http.task.router import router
    assert "task" in router.tags


# --- endpoint smoke with a stub TaskService via injector --------------------

class _StubTaskService:
    """Minimal stub satisfying the TaskService Protocol face used by router."""

    def get(self, task_id: str) -> Any:
        return {"task_id": task_id, "user_id": "u1", "status": "drafting",
                "spec": {"metadata": {"id": task_id, "title": "x"}},
                "execution_graph": None, "loop_round": 0}

    def list_by_user(self, user_id: str, limit: int = 50) -> list[Any]:
        return []

    def progress(self, task_id: str) -> dict:
        return {"task_id": task_id, "status": "executing", "loop_round": 1,
                "done": 1, "total": 3, "nodes": []}

    def create(self, title: str, source: str = "api", background: str = "") -> Any:
        return {"task_id": "t1", "status": "drafting", "seq": 1}

    def clarify(self, task_id: str, patch: dict, confirmed: bool = False) -> Any:
        return self.get(task_id)

    def on_event(self, event: Any) -> Any:
        return self.get(getattr(event, "task_id", "t1"))

    def latest_seq(self, task_id: str) -> int:
        return 1

    def claim_node(self, task_id: str, node_id: str, executor_id: str) -> Any:
        return {"node_id": node_id, "executor_id": executor_id,
                "run_mode": "single_bot", "accept_token": ""}

    # --- canvas (secondary panel) query face (Phase 0.7, plan §1.4b) -------
    def get_task_graph(self, task_id: str) -> Any:
        return {
            "task_id": task_id,
            "status": "running",
            "loop_round": 0,
            "definition_meta": None,
            "nodes": [{"node_id": "n1", "display_name": "n", "status": "running"}],
            "edges": [],
        }

    def get_node_detail(self, task_id: str, node_id: str) -> Any:
        return {"node_id": node_id, "display_name": "n", "status": "running"}

    def get_sub_dag(self, task_id: str, node_id: str) -> Any:
        return {
            "task_id": task_id,
            "status": "running",
            "loop_round": 0,
            "nodes": [{"node_id": "sm-n1", "display_name": "x", "status": "completed"}],
            "edges": [],
        }

    def history(self, task_id: str, after_seq: int = 0) -> list[Any]:
        # Real TaskEvent objects (router maps via getattr). seq-ordered trace.
        from agentclaw.community.core.task.domain.events import EventKind, TaskEvent
        all_events = [
            TaskEvent(task_id=task_id, seq=1, kind=EventKind.TASK_CREATED,
                      payload={"title": "x"}, reported=False, occurred_at="2026-07-30T00:00:00"),
            TaskEvent(task_id=task_id, seq=2, kind=EventKind.TASK_CLARIFIED,
                      payload={"patch": {}, "confirmed": True}, reported=False, occurred_at="2026-07-30T00:00:01"),
            TaskEvent(task_id=task_id, seq=3, kind=EventKind.NODE_RUNNING,
                      payload={"node_id": "n1"}, reported=True, occurred_at="2026-07-30T00:00:02"),
        ]
        return [e for e in all_events if e.seq > after_seq]


class _StubTaskScheduler:
    """Minimal stub satisfying the TaskScheduler Protocol face used by router
    (start / tick / on_event). /events 落态 fold 后泵 on_event → 此 stub 不真跑。"""

    def start(self, task_id: str) -> Any:
        return None

    def tick(self, task_id: str) -> Any:
        return {"task_id": task_id, "action": "noop", "reason": "stub"}

    def on_event(self, event: Any) -> Any:
        return None


def _client_with_stub() -> TestClient:
    from agentclaw.community.api.task import TaskSchedulerProtocol, TaskServiceProtocol
    from agentclaw.community.adapters.http.task.router import router
    app = FastAPI()
    app.include_router(router)
    inj = Injector([])
    # Router resolves Injected(TaskServiceProtocol) — the api-layer service api
    # (adapters → api, not → core). The stub structurally satisfies it.
    inj.binder.bind(TaskServiceProtocol, to=_StubTaskService(), scope=singleton)
    # /events 折叠后泵 scheduler.on_event(编排反应);start/tick 亦注入 scheduler。
    inj.binder.bind(TaskSchedulerProtocol, to=_StubTaskScheduler(), scope=singleton)
    attach_injector(app, inj)
    return TestClient(app)


def test_post_create_task_returns_200():
    client = _client_with_stub()
    r = client.post("/api/tasks/create", json={"title": "fix PR", "source": "api"})
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == "t1"
    assert body["status"] == "drafting"


def test_get_task_detail_returns_200():
    client = _client_with_stub()
    r = client.get("/api/tasks/t1")
    assert r.status_code == 200
    assert r.json()["task_id"] == "t1"


def test_get_progress_returns_200():
    client = _client_with_stub()
    r = client.get("/api/tasks/t1/progress")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "executing"
    assert body["loop_round"] == 1


def test_post_clarify_returns_200():
    client = _client_with_stub()
    r = client.post("/api/tasks/t1/clarify", json={"patch": {"goal": "x"}})
    assert r.status_code == 200


def test_post_event_report_returns_200():
    client = _client_with_stub()
    r = client.post("/api/tasks/t1/events",
                    json={"kind": "node.accepted", "payload": {"node_id": "n1"}})
    assert r.status_code == 200


# --- canvas (secondary panel) endpoints (Phase 0.7, plan §7.2) --------------

def test_get_task_graph_returns_200():
    client = _client_with_stub()
    r = client.get("/api/tasks/t1/graph")
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == "t1"
    assert body["status"] == "running"
    assert body["nodes"][0]["node_id"] == "n1"


def test_get_node_detail_returns_200():
    client = _client_with_stub()
    r = client.get("/api/tasks/t1/nodes/n1")
    assert r.status_code == 200
    assert r.json()["node_id"] == "n1"


def test_get_sub_dag_returns_200():
    client = _client_with_stub()
    r = client.get("/api/tasks/t1/nodes/n1/sub-dag")
    assert r.status_code == 200
    body = r.json()
    assert body["nodes"][0]["node_id"] == "sm-n1"


def test_get_task_history_returns_200():
    client = _client_with_stub()
    r = client.get("/api/tasks/t1/history")
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == "t1"
    assert body["total"] == 3
    evs = body["items"]
    assert [e["seq"] for e in evs] == [1, 2, 3]  # seq-ordered
    assert evs[0]["kind"] == "task.created"
    assert evs[2]["reported"] is True
    assert evs[0]["occurred_at"] == "2026-07-30T00:00:00"


def test_get_task_history_after_seq_filter():
    client = _client_with_stub()
    r = client.get("/api/tasks/t1/history?after_seq=1")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert [e["seq"] for e in body["items"]] == [2, 3]


# --- BBS claim/release routes (Task 5) + IllegalTransitionError→409 (Task 4) --
# Real-service harness: the stub service can't exercise CAS conflicts / 403 /
# 409, so we wire a real in-memory TaskService. The bare FastAPI app here mirrors
# app.py's IllegalTransitionError→409 translation (production handler lives in
# adapters/http/app.py; without it the second claim's IllegalTransitionError
# would propagate uncaught instead of returning 409).

def _real_service():
    from agentclaw.community.core.task.services import TaskService
    from agentclaw.community.plugins.community.task.in_memory_repos import (
        InMemoryTaskEventRepo,
        InMemoryTaskRepo,
    )
    from agentclaw.community.plugins.community.task.panel_publisher import (
        RecordingPanelPublisher,
    )
    return TaskService(InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher())


def _task_with_pending_node(svc) -> tuple[str, str]:
    """create → clarify(confirmed=True → DEFINED) → init_execution_graph → add_node.
    Returns (task_id, node_id) with the node in PENDING (claimable) state."""
    from agentclaw.community.core.task.domain.models import NodeType, RunMode, SubTaskSpec
    t = svc.create(title="t")
    svc.clarify(t.id, {"summary": "s"})
    svc.clarify(t.id, {}, confirmed=True)
    task = svc.get(t.id)
    svc.init_execution_graph(task)
    # add_node signature: (task_id, node|SubTaskSpec, parent_node, node_type, executor="")
    # — node_type is required (Correction 1: the brief omitted it → TypeError).
    svc.add_node(
        task.id,
        SubTaskSpec(node_id="n1", spec="a", run_mode=RunMode.BBS),
        "n_execute_start",
        NodeType.DISPATCH,
    )
    return t.id, "n1"


def _client_with_real(svc) -> TestClient:
    from agentclaw.community.api.task import TaskSchedulerProtocol, TaskServiceProtocol
    from agentclaw.community.adapters.http.task.router import router
    from agentclaw.community.core.errors import DomainError, Forbidden
    from agentclaw.community.core.task.domain.state_machine import IllegalTransitionError
    from fastapi.responses import JSONResponse

    app = FastAPI()
    app.include_router(router)
    # Mirror app.py's error translation for the paths under test: the bare app
    # has no app-level handlers, so without these the routes' raised errors
    # propagate uncaught (TestClient re-raises) instead of returning 409/403.
    # Forbidden (non-assignee release) → 403; IllegalTransitionError (concurrent
    # claim / illegal source state) → 409 — same status mapping as app.py.
    @app.exception_handler(DomainError)
    async def _domain_error_handler(request, exc):  # noqa: ANN001
        status = 403 if isinstance(exc, Forbidden) else 500
        return JSONResponse(status_code=status, content={"detail": exc.detail})

    @app.exception_handler(IllegalTransitionError)
    async def _illegal_transition_handler(request, exc):  # noqa: ANN001
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    inj = Injector([])
    inj.binder.bind(TaskServiceProtocol, to=svc, scope=singleton)
    inj.binder.bind(TaskSchedulerProtocol, to=_StubTaskScheduler(), scope=singleton)
    attach_injector(app, inj)
    return TestClient(app)


@pytest.fixture
def real_client_with_running_node():
    svc = _real_service()
    task_id, node_id = _task_with_pending_node(svc)
    return _client_with_real(svc), task_id, node_id


def test_claim_returns_200_with_lease(real_client_with_running_node):
    client, task_id, node_id = real_client_with_running_node
    r = client.post(
        f"/api/tasks/{task_id}/nodes/{node_id}/claim",
        json={"executor_id": "bot-A", "run_mode": "bbs"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["node_id"] == node_id
    assert body["run_mode"] == "bbs"
    assert body["lease_until"]  # 非空 ISO


def test_release_by_assignee_returns_200(real_client_with_running_node):
    client, task_id, node_id = real_client_with_running_node
    client.post(
        f"/api/tasks/{task_id}/nodes/{node_id}/claim",
        json={"executor_id": "bot-A", "run_mode": "bbs"},
    )
    r = client.post(
        f"/api/tasks/{task_id}/nodes/{node_id}/release",
        json={"executor_id": "bot-A"},
    )
    assert r.status_code == 200
    assert r.json()["outcome"] == "handoff"


def test_release_non_assignee_returns_403(real_client_with_running_node):
    client, task_id, node_id = real_client_with_running_node
    client.post(
        f"/api/tasks/{task_id}/nodes/{node_id}/claim",
        json={"executor_id": "bot-A", "run_mode": "bbs"},
    )
    r = client.post(
        f"/api/tasks/{task_id}/nodes/{node_id}/release",
        json={"executor_id": "bot-B"},
    )
    assert r.status_code == 403


def test_illegal_transition_maps_to_409(real_client_with_running_node):
    client, task_id, node_id = real_client_with_running_node
    # 第一个 claim 200;第二个并发 claim 同节点 → IllegalTransitionError → 409
    r1 = client.post(
        f"/api/tasks/{task_id}/nodes/{node_id}/claim",
        json={"executor_id": "bot-A", "run_mode": "bbs"},
    )
    assert r1.status_code == 200
    r2 = client.post(
        f"/api/tasks/{task_id}/nodes/{node_id}/claim",
        json={"executor_id": "bot-B", "run_mode": "bbs"},
    )
    assert r2.status_code == 409


def test_router_registers_claim_release_routes():
    from agentclaw.community.adapters.http.task.router import router
    paths = {getattr(r, "path", None) for r in router.routes}
    assert "/api/tasks/{task_id}/nodes/{node_id}/claim" in paths
    assert "/api/tasks/{task_id}/nodes/{node_id}/release" in paths