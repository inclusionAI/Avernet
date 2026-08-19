from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from fastapi.testclient import TestClient
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.task.auth import (
    CallbackAuthenticator, NoopCallbackAuthenticator,
)
from agentclaw.community.adapters.http.task.router import task_callback_router
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.errors import NodeNotFoundError, TaskStateError
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, RuntimeInfo, Status,
    TaskExecutionGraph, TaskNode, TaskSpec,
)
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry, InMemoryCallbackCorrelationRegistry,
)


class _StubCallback:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def start_run(self, data):
        self.calls.append(("start", data))

    async def report_result(self, data):
        self.calls.append(("result", data))


def _raise(exc):
    async def _f(data):
        raise exc
    return _f


def _make_node(task_id, node_id, status) -> TaskNode:
    return TaskNode(
        node_id=node_id, task_id=task_id, status=status,
        task_spec=TaskSpec(Metadata(task_id, "T", "do"), Context("bg"),
                           Goal("O", [AcceptanceCriteria("a1", "d")])),
        run_info=RuntimeInfo(), node_run_graph=None,  # type: ignore[arg-type]
    )


class _StubService:
    """最小 TaskServiceProtocol stub:router 仅用 .callback 与 get_task_dashboard。"""

    def __init__(self) -> None:
        self.callback = _StubCallback()
        self._node_status: dict[tuple[str, str], Status] = {}

    def set_node_status(self, task_id, node_id, status) -> None:
        self._node_status[(task_id, node_id)] = status

    def get_task_dashboard(self, task_id, node_id=None) -> TaskExecutionGraph:
        g = TaskExecutionGraph(run_id=1, loop_round=0, status=Status.PENDING)
        for (tid, nid), st in list(self._node_status.items()):
            if tid == task_id:
                g.tasks.append(_make_node(tid, nid, st))
        return g


class _StubTaskModule(Module):
    """绑定 callback router 三个 Protocol 到 stub/Noop/InMemory。"""

    @singleton
    @provider
    def svc(self) -> TaskServiceProtocol:
        return _StubService()  # type: ignore[return-value]

    @singleton
    @provider
    def auth(self) -> CallbackAuthenticator:
        return NoopCallbackAuthenticator()

    @singleton
    @provider
    def reg(self) -> CallbackCorrelationRegistry:
        return InMemoryCallbackCorrelationRegistry()


@pytest.fixture
def client():
    injector = Injector([_StubTaskModule()])
    svc = injector.get(TaskServiceProtocol)
    app = FastAPI()
    app.include_router(task_callback_router)
    attach_injector(app, injector)
    return TestClient(app), svc


def _body(node=False, **kw):
    d = dict(task_id="t1", workflow_source="bcn", workflow_id="w7",
             workflow_instance_id="i1", status="COMPLETED", is_success=True)
    d.update(kw)
    if node:
        d.setdefault("node_id", "c1")
    return d


class TestRouter:
    def test_workflow_result_success(self, client):
        c, svc = client
        r = c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=_body(loop_task_id="t1::root1"))
        assert r.status_code == 200, r.text
        assert svc.callback.calls[0][0] == "result"

    def test_node_result_success(self, client):
        c, svc = client
        r = c.post("/api/v1/collaboration/tasks/callback/node_result", json=_body(node=True))
        assert r.status_code == 200, r.text
        assert svc.callback.calls[0][1].loop_task_id == "t1::c1"

    def test_workflow_start_success(self, client):
        c, svc = client
        r = c.post("/api/v1/collaboration/tasks/callback/workflow_start",
                   json=_body(loop_task_id="t1::root1", status="RUNNING"))
        assert r.status_code == 200, r.text
        assert svc.callback.calls[0][0] == "start"

    def test_node_start_success(self, client):
        c, svc = client
        r = c.post("/api/v1/collaboration/tasks/callback/node_start", json=_body(node=True, status="RUNNING"))
        assert r.status_code == 200, r.text
        assert svc.callback.calls[0][0] == "start"

    def test_result_idempotent_when_already_terminal(self, client):
        c, svc = client
        svc.callback.report_result = _raise(TaskStateError("DONE->DONE"))
        svc.set_node_status("t1", "root1", Status.DONE)
        r = c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=_body(loop_task_id="t1::root1"))
        assert r.status_code == 200, r.text  # 幂等 ack

    def test_result_409_when_illegal(self, client):
        c, svc = client
        svc.callback.report_result = _raise(TaskStateError("PENDING->DONE"))
        svc.set_node_status("t1", "root1", Status.PENDING)  # 非终态→409
        r = c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=_body(loop_task_id="t1::root1"))
        assert r.status_code == 409, r.text

    def test_start_409_on_stale(self, client):
        c, svc = client
        svc.callback.start_run = _raise(TaskStateError("stale"))
        r = c.post("/api/v1/collaboration/tasks/callback/node_start", json=_body(node=True, status="RUNNING"))
        assert r.status_code == 409, r.text

    def test_not_found_404(self, client):
        c, svc = client
        svc.callback.report_result = _raise(NodeNotFoundError("x"))
        r = c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=_body(loop_task_id="t1::root1"))
        assert r.status_code == 404, r.text

    def test_correlation_error_400(self, client):
        c, _ = client
        # task 级无回声 + 空 registry → CallbackCorrelationError → 400
        r = c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=_body())  # 无 loop_task_id,registry 空
        assert r.status_code == 400, r.text

    def test_validation_422(self, client):
        c, _ = client
        r = c.post("/api/v1/collaboration/tasks/callback/node_result", json={"task_id": "t1"})  # 缺必填
        assert r.status_code == 422, r.text