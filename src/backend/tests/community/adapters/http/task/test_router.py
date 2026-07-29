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
    AmendTaskRequest,
    CreateTaskRequest,
    EventReportRequest,
    FinalizePlanRequest,
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
    r = TaskCreatedResponse(task_id="t1", status="intake", seq=1)
    assert r.task_id == "t1"
    assert r.status == "intake"
    assert r.seq == 1


def test_task_detail_response_minimal():
    r = TaskDetailResponse(task_id="t1", user_id="u1", status="intake", loop_round=0)
    assert r.task_id == "t1"
    assert r.status == "intake"
    assert r.nodes == []


def test_amend_and_finalize_request_shapes():
    a = AmendTaskRequest(patch={"goal": "x"})
    assert a.patch == {"goal": "x"}
    f = FinalizePlanRequest(plan_payload={"sub_tasks": []})
    assert f.plan_payload == {"sub_tasks": []}


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
    assert "/api/tasks" in paths                      # POST create, GET list
    assert "/api/tasks/{task_id}" in paths            # GET detail
    assert "/api/tasks/{task_id}/amend" in paths       # POST amend
    assert "/api/tasks/{task_id}/plan" in paths        # POST finalize_plan
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
        return {"task_id": task_id, "user_id": "u1", "status": "intake",
                "spec": {"metadata": {"id": task_id, "title": "x"}},
                "execution_graph": None, "loop_round": 0}

    def list_by_user(self, user_id: str, limit: int = 50) -> list[Any]:
        return []

    def progress(self, task_id: str) -> dict:
        return {"task_id": task_id, "status": "executing", "loop_round": 1,
                "done": 1, "total": 3, "nodes": []}

    def create(self, title: str, source: str = "api", background: str = "") -> Any:
        return {"task_id": "t1", "status": "intake", "seq": 1}

    def amend(self, task_id: str, patch: dict) -> Any:
        return self.get(task_id)

    def finalize_plan(self, task_id: str, plan: Any) -> Any:
        return self.get(task_id)

    def on_event(self, event: Any) -> Any:
        return self.get(getattr(event, "task_id", "t1"))

    def claim_node(self, task_id: str, node_id: str, executor_id: str) -> Any:
        return {"node_id": node_id, "executor_id": executor_id,
                "run_mode": "single_bot", "accept_token": ""}

    # --- canvas (secondary panel) query face (Phase 0.7, plan §1.4b) -------
    def get_task_graph(self, task_id: str) -> Any:
        return {
            "task_id": task_id,
            "root_phase": "executing",
            "graph_status": "on_plaza",
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
            "root_phase": "executing",
            "graph_status": "on_plaza",
            "loop_round": 0,
            "nodes": [{"node_id": "sm-n1", "display_name": "x", "status": "completed"}],
            "edges": [],
        }


def _client_with_stub() -> TestClient:
    from agentclaw.community.api.task import TaskService
    from agentclaw.community.adapters.http.task.router import router
    app = FastAPI()
    app.include_router(router)
    inj = Injector([])
    inj.binder.bind(TaskService, to=_StubTaskService(), scope=singleton)
    attach_injector(app, inj)
    return TestClient(app)


def test_post_create_task_returns_200():
    client = _client_with_stub()
    r = client.post("/api/tasks", json={"title": "fix PR", "source": "api"})
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == "t1"
    assert body["status"] == "intake"


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


def test_post_amend_returns_200():
    client = _client_with_stub()
    r = client.post("/api/tasks/t1/amend", json={"patch": {"goal": "x"}})
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
    assert body["root_phase"] == "executing"
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