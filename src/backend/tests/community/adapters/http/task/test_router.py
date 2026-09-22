from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from fastapi.testclient import TestClient
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.task.auth import (
    CallbackAuthenticator, NoopCallbackAuthenticator,
)
from agentclaw.community.adapters.http.task.router import router, task_callback_router
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.errors import NotFound
from agentclaw.community.core.task.domain.errors import NodeNotFoundError, TaskStateError
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, RuntimeInfo, Status,
    TaskExecutionGraph, TaskNode, TaskSpec,
)
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry, InMemoryCallbackCorrelationRegistry,
)
from agentclaw.community.di.modules.infrastructure.community.task_runner_integration import (
    BcsTokenProviderImpl,
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
        task_spec=TaskSpec(context=Context("bg", title="T"), goal=Goal("O", [AcceptanceCriteria("a1", "d")])),
        run_info=RuntimeInfo(), node_run_graph=None,  # type: ignore[arg-type]
    )


class _StubService:
    """最小 TaskServiceProtocol stub:router 仅用 .callback 与 get_task_dashboard。"""

    def __init__(self) -> None:
        self.callback = _StubCallback()
        self._node_status: dict[tuple[str, str], Status] = {}
        self._execution_config: dict = {}
        self.relay_exception: Exception | None = None

    def set_node_status(self, task_id, node_id, status) -> None:
        self._node_status[(task_id, node_id)] = status

    def set_execution_config(self, execution_config: dict) -> None:
        self._execution_config = execution_config

    def get_task_dashboard(self, task_id, node_id=None) -> TaskExecutionGraph:
        g = TaskExecutionGraph(
            run_id=1,
            loop_round=0,
            status=Status.PENDING,
            extend_props={"execution_config": self._execution_config},
        )
        for (tid, nid), st in list(self._node_status.items()):
            if tid == task_id:
                g.tasks.append(_make_node(tid, nid, st))
        return g

    async def apply_manager_worker_event(self, raw):  # noqa: ANN001 stub
        """manager_worker 分流回调 stub:router BCN 分支调它;记录便于断言。"""
        self.callback.calls.append(("manager_worker", raw))

    async def report_task_event(self, **kwargs):
        self.callback.calls.append(("relay", kwargs))
        if self.relay_exception is not None:
            raise self.relay_exception
        return {"ok": True, "relay_turn": "turn-1"}

    def record_relay_callback_success(self, **kwargs):
        self.callback.calls.append(("relay_success", kwargs))

    def record_relay_callback_error(self, **kwargs):
        self.callback.calls.append(("relay_error", kwargs))

    async def search_task_candidates(self, **kwargs):
        self.callback.calls.append(("search", kwargs))
        return {"candidates": [], "total": 0}

    async def dispatch_task(self, **kwargs):
        self.callback.calls.append(("dispatch", kwargs))
        return {"ok": True, "node_id": kwargs["target_node_id"]}

    def claim_bbs_task(self, task_id, bot_id, node_id=None, claim_id=None):
        self.callback.calls.append(("bbs_claim", (task_id, bot_id, node_id, claim_id)))
        return SimpleNamespace(node_id=task_id)


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
        return CallbackDataEnricher(BcsTokenProviderImpl(base_url="http://bcs"), http_client=None)


@pytest.fixture
def client():
    injector = Injector([_StubTaskModule()])
    svc = injector.get(TaskServiceProtocol)
    app = FastAPI()
    app.include_router(task_callback_router)
    attach_injector(app, injector)
    return TestClient(app), svc


@pytest.fixture
def task_client():
    injector = Injector([_StubTaskModule()])
    svc = injector.get(TaskServiceProtocol)
    app = FastAPI()
    app.include_router(router)
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
    def test_unified_callback_report_accepts_typed_relay_event(self, task_client):
        client, svc = task_client
        response = client.post(
            "/api/v1/collaboration/tasks/callback/report",
            json={
                "task_id": "t1",
                "node_id": "t1",
                "event_type": "EXECUTION_RESULT",
                "event_id": "event-report-1",
                "holder_id": "bot-1",
                "progress_reason": "execution complete",
                "payload": {"success": True, "output": {"result": "done"}},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["relay_turn"] == "turn-1"
        assert [call[0] for call in svc.callback.calls] == ["relay", "relay_success"]
        success = svc.callback.calls[1][1]
        assert success["event_type"] == "EXECUTION_RESULT"
        assert success["event_id"] == "event-report-1"

    def test_legacy_callback_rejected_for_relay_task(self, task_client):
        client, svc = task_client
        svc.set_execution_config({"orchestration_mode": "relay"})
        response = client.post(
            "/api/v1/collaboration/tasks/callback/report",
            json={
                "task_id": "t1",
                "node_id": "c1",
                "status": "SUCCESS",
                "output": "误用旧协议直接完成",
                "acceptance_result": {"verdict": "DONE", "acceptances_metric": [], "gaps": []},
                "extend_props": {},
            },
        )
        assert response.status_code == 409, response.text
        # 拒绝即录(record_relay_callback_error 通道,error_phase=reject_legacy_relay_result)
        assert [call[0] for call in svc.callback.calls] == ["relay_error"]
        rejected = svc.callback.calls[0][1]
        assert rejected["task_id"] == "t1"
        assert rejected["node_id"] == "c1"
        assert rejected["error_phase"] == "reject_legacy_relay_result"
        assert "EXECUTION_RESULT" in rejected["error_msg"]

    def test_task_level_legacy_callback_rejected_for_relay_task(self, client):
        client, svc = client
        svc.set_execution_config({"orchestration_mode": "relay"})
        response = client.post(
            "/api/v1/collaboration/tasks/callback/workflow_result",
            json=_body(task_id="t1", status="COMPLETED", is_success=True),
        )
        assert response.status_code == 409, response.text
        # 拒绝即录(node 锚回 task_id 根)
        assert [call[0] for call in svc.callback.calls] == ["relay_error"]
        rejected = svc.callback.calls[0][1]
        assert rejected["task_id"] == "t1"
        assert rejected["error_phase"] == "reject_legacy_relay_result"

    def test_typed_relay_event_accepted_for_relay_task(self, task_client):
        client, svc = task_client
        svc.set_execution_config({"orchestration_mode": "relay"})
        response = client.post(
            "/api/v1/collaboration/tasks/callback/report",
            json={
                "task_id": "t1",
                "node_id": "c1",
                "event_type": "EXECUTION_RESULT",
                "event_id": "event-relay-1",
                "holder_id": "bot-1",
                "progress_reason": "execution complete",
                "payload": {"success": True, "output": {"result": "done"}},
            },
        )
        assert response.status_code == 200, response.text
        assert [call[0] for call in svc.callback.calls] == ["relay", "relay_success"]

    def test_relay_callback_validation_error_records_diagnostic_trajectory(self, task_client):
        client, svc = task_client
        response = client.post(
            "/api/v1/collaboration/tasks/callback/report",
            json={
                "task_id": "t1",
                "node_id": "t1",
                "event_type": "EXECUTION_RESULT",
                "event_id": "invalid-1",
                "progress_reason": "execution complete",
                "payload": {"success": True},
            },
        )
        assert response.status_code == 422, response.text
        kind, details = svc.callback.calls[0]
        assert kind == "relay_error"
        assert details["error_phase"] == "validation"
        assert details["exception_type"] == "ValidationError"
        assert details["task_id"] == "t1"

    def test_relay_callback_processing_error_records_diagnostic_trajectory(self, task_client):
        client, svc = task_client
        svc.relay_exception = TaskStateError("relay node not found")
        response = client.post(
            "/api/v1/collaboration/tasks/callback/report",
            json={
                "task_id": "t1",
                "node_id": "missing",
                "event_type": "EXECUTION_RESULT",
                "event_id": "event-failed-1",
                "holder_id": "bot-1",
                "progress_reason": "execution complete",
                "payload": {"success": True},
            },
        )
        assert response.status_code == 409, response.text
        assert [call[0] for call in svc.callback.calls] == ["relay", "relay_error"]
        details = svc.callback.calls[1][1]
        assert details["error_phase"] == "processing"
        assert details["exception_type"] == "TaskStateError"
        assert details["error_msg"] == "relay node not found"

    def test_generic_search_returns_catalog_without_deciding(self, task_client):
        client, svc = task_client
        response = client.post(
            "/api/v1/collaboration/tasks/search",
            json={"query": "完成下一步研究"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"] == {"candidates": [], "total": 0}
        assert svc.callback.calls == [
            (
                "search",
                {"query": "完成下一步研究"},
            )
        ]

    def test_generic_dispatch_consumes_persisted_decision(self, task_client):
        client, svc = task_client
        response = client.post(
            "/api/v1/collaboration/tasks/dispatch",
            json={
                "task_id": "t1",
                "origin_node_id": "origin",
                "target_node_id": "next",
                "holder_id": "bot-1",
                "relay_turn": "turn-1",
                "dispatch_id": "dispatch-1",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["node_id"] == "next"
        assert svc.callback.calls[0][0] == "dispatch"

    def test_relay_bbs_claim_identifies_scoped_node(self, task_client):
        client, svc = task_client
        response = client.post(
            "/api/v1/collaboration/tasks/bbs/claim",
            json={"task_id": "t1", "node_id": "bbs-step", "bot_id": "bbs-bot"},
        )
        assert response.status_code == 200, response.text
        assert svc.callback.calls == [
            ("bbs_claim", ("t1", "bbs-bot", "bbs-step", None))
        ]

    def test_relay_event_uses_typed_task_service_entry(self, client):
        c, svc = client
        body = {
            "task_id": "t1",
            "node_id": "t1",
            "event_type": "EXECUTION_RESULT",
            "event_id": "event-1",
            "holder_id": "bot-1",
            "progress_reason": "execution complete",
            "payload": {"success": True, "output": {"result": "done"}},
        }
        r = c.post(
            "/api/v1/collaboration/tasks/callback/workflow_result", json=body
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["relay_turn"] == "turn-1"
        assert svc.callback.calls[0][0] == "relay"

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
