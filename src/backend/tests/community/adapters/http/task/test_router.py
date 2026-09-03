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
from agentclaw.community.core.errors import NotFound
from agentclaw.community.core.task.domain.errors import NodeNotFoundError, TaskStateError
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, RuntimeInfo, Status,
    TaskExecutionGraph, TaskNode, TaskSpec,
)
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry, InMemoryCallbackCorrelationRegistry,
)
from agentclaw.community.core.task.task_runner.client.bcs_token_provider import (
    LocalBcsTokenProvider,
)
from agentclaw.community.core.task.task_runner.client.callback_data_enricher import (
    CallbackDataEnricher,
)


class _StubCallback:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def start_run(self, data):
        self.calls.append(("start", data))

    async def report_result(self, data):
        self.calls.append(("result", data))

    async def ingest(self, data):
        self.calls.append(("ingest", data))


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

    async def apply_manager_worker_event(self, raw):  # noqa: ANN001 stub
        """manager_worker 分流回调 stub:router BCN 分支调它;记录便于断言。"""
        self.callback.calls.append(("manager_worker", raw))


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

    @singleton
    @provider
    def enricher(self) -> CallbackDataEnricher:
        # callback router 注入 CallbackDataEnricher 构 execution_graph;纯单测不真连 BCS,
        # 传 localhost base_url + 短连,fetch 失败由 enrich_bcn 兜底回退事件体建图(不抛)。
        return CallbackDataEnricher(LocalBcsTokenProvider.from_env(), http_client=None)


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
        assert svc.callback.calls[0][1].data["loop_task_id"] == "t1::c1"

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
        # 非终态重投:report_result 抛 TaskStateError,非幂等 → 不进幂等分支。
        # ``@envelope_errors`` 经 ``ENVELOPE_ERRORS`` 把它映射为 409 ErrorEnvelope,
        # 该映射在 handler 帧内完成,不依赖本 fixture 的中央 handler。
        c, svc = client
        svc.callback.report_result = _raise(TaskStateError("PENDING->DONE"))
        svc.set_node_status("t1", "root1", Status.PENDING)  # 非终态
        r = c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=_body(loop_task_id="t1::root1"))
        assert r.status_code == 409, r.text
        assert r.json()["code"] == 409000

    def test_start_409_on_stale(self, client):
        # start 抛 TaskStateError;disposition!=result 不进幂等分支 → 409 ErrorEnvelope。
        c, svc = client
        svc.callback.start_run = _raise(TaskStateError("stale"))
        r = c.post("/api/v1/collaboration/tasks/callback/node_start", json=_body(node=True, status="RUNNING"))
        assert r.status_code == 409, r.text
        assert r.json()["code"] == 409000

    def test_not_found_404(self, client):
        # report_result 抛 NodeNotFoundError → ``ENVELOPE_ERRORS`` 映射为 404 ErrorEnvelope。
        c, svc = client
        svc.callback.report_result = _raise(NodeNotFoundError("x"))
        r = c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=_body(loop_task_id="t1::root1"))
        assert r.status_code == 404, r.text
        assert r.json()["code"] == 404000

    def test_correlation_error_400(self, client):
        # task 级无回声 + 空 registry → translate 抛 NotFound(core.errors;原计划 CallbackCorrelationError
        # 尚未落地)。未被 @envelope_errors 映射、本 fixture 无中央 handler → 异常经 TestClient 上抛
        # → 断言领域错误上抛。
        c, _ = client
        with pytest.raises(NotFound):
            c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=_body())  # 无 loop_task_id,registry 空

    def test_validation_422(self, client):
        c, _ = client
        r = c.post("/api/v1/collaboration/tasks/callback/node_result", json={"task_id": "t1"})  # 缺必填
        assert r.status_code == 422, r.text

    def test_claw_mind_callback_ingests_only(self, client):
        # ClawMind HttpCallbackPayload → 只落库(ingest),不推进引擎(不走 start/result)
        c, svc = client
        body = {"workflow_id": "wf-1", "flow_id": "fl-1", "status": "succeeded",
                "ext_info": {"flow_runs": {"status": "succeeded", "origin_session_id": "S-9"},
                             "node_executions": []}}
        r = c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=body)
        assert r.status_code == 200, r.text
        assert svc.callback.calls and svc.callback.calls[0][0] == "ingest"
        assert all(k == "ingest" for k, _ in svc.callback.calls)

    def test_bcn_callback_ingests_only(self, client):
        # BCN CloudEvent(已处理事件)→ 只落库(ingest),不推进引擎
        c, svc = client
        evt = {"spec_version": "1.0", "event_id": "e1",
               "event_type": "state_machine.node.completed", "source": "bcs",
               "scope": {"group_id": "g1", "session_id": "s1", "run_id": "r1"},
               "stream": {"key": "k", "sequence": 1}, "actor": {"type": "bot", "id": "b"},
               "data": {"run_id": "r1", "node_id": "n1", "outcome": "success", "output": {"x": 1}}}
        r = c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=evt)
        assert r.status_code == 200, r.text
        assert svc.callback.calls and svc.callback.calls[0][0] == "ingest"
        assert all(k == "ingest" for k, _ in svc.callback.calls)

    def test_bcn_unhandled_event_acks_without_ingest(self, client):
        c, svc = client
        evt = {"spec_version": "1.0", "event_id": "e2", "event_type": "message.created",
               "source": "bcs", "scope": {"group_id": "g1", "session_id": "s1"},
               "stream": {"key": "k", "sequence": 1}, "actor": {}, "data": {}}
        r = c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=evt)
        assert r.status_code == 200, r.text
        assert svc.callback.calls == []  # 非处理事件:不落库、不推进