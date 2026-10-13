from __future__ import annotations

from types import SimpleNamespace
import importlib

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from fastapi.testclient import TestClient
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.task.auth import (
    CallbackAuthenticator, NoopCallbackAuthenticator,
)
from agentclaw.community.adapters.http.task.router import router, task_callback_router
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.errors import NotFound
from agentclaw.community.core.task.domain.errors import NodeNotFoundError, TaskStateError
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, RuntimeInfo, Status,
    TaskExecutionGraph, TaskNode, TaskSpec, TaskSourceType, TaskOpResult,
    TaskReportResult, TaskReportStatus,
)
from agentclaw.community.core.task.domain.intake import (
    IntakeTarget, IntakeTargetType, TaskExecutionMode, TaskIntakeResult,
    TaskIntakeStatus,
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
        self.intake_result: TaskIntakeResult | None = None

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
                node = _make_node(tid, nid, st)
                if self._execution_config.get("intake_session_id"):
                    node.run_info.assignee = "bot-1"
                    node.run_info.extend_props["relay_token"] = "run-1"
                g.tasks.append(node)
        return g

    async def report(self, command):
        self.callback.calls.append(("new_relay_report", command))
        return TaskReportResult(TaskReportStatus.APPLIED)

    async def apply_manager_worker_event(self, raw):  # noqa: ANN001 stub
        """manager_worker 分流回调 stub:router BCN 分支调它;记录便于断言。"""
        self.callback.calls.append(("manager_worker", raw))

    async def dispatch_node(self, task_id, node_id):
        self.callback.calls.append(("dispatch_node", (task_id, node_id)))
        return SimpleNamespace(node_id=node_id)

    async def intake(self, command, context):
        self.callback.calls.append(("intake", (command, context)))
        return self.intake_result or self._confirmed_intake()

    def get_confirmed_intake(self, session_id, owner_user_id):
        self.callback.calls.append(("get_confirmed_intake", (session_id, owner_user_id)))
        if session_id != "tis-1" or owner_user_id != "owner-1":
            raise TaskStateError("intake owner mismatch")
        return self._confirmed_intake()

    @staticmethod
    def _confirmed_intake():
        return TaskIntakeResult(
            session_id="tis-1", owner_user_id="owner-1", source_type=TaskSourceType.API,
            status=TaskIntakeStatus.CONFIRMED,
            target=IntakeTarget(IntakeTargetType.BOT, "bot-1"),
            task_spec=TaskSpec(
                context=Context("", title="上线", extend_props={
                    "deliverables": ["上线记录"], "constraints": [],
                }),
                goal=Goal("上线模型", [AcceptanceCriteria("ac1", "完成灰度")]),
            ),
            create_time_ms=1, update_time_ms=2,
        )

    async def execute(self, request):
        self.callback.calls.append(("execute", request))
        return TaskOpResult(task_id="task-1", node_id="task-1", success=True, run_id=1)

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


@pytest.fixture
def human_task_client():
    injector = Injector([_StubTaskModule()])
    svc = injector.get(TaskServiceProtocol)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="id-1", staffId="owner-1", operatorName="owner",
    )
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
    def test_new_relay_rejects_legacy_node_write(self, task_client):
        client, svc = task_client
        svc.set_execution_config({"orchestration_mode": "relay", "intake_session_id": "tis-1"})
        svc.set_node_status("t1", "t1", Status.RUNNING)
        response = client.post(
            "/api/v1/collaboration/tasks/nodes/update",
            json={"task_id": "t1", "node_id": "t1", "status": "SUCCESS"},
        )
        assert response.status_code != 200
        assert svc.callback.calls == []

    def test_new_relay_bbs_claim_cannot_spoof_bot_id(self, task_client, monkeypatch):
        client, svc = task_client
        svc.set_execution_config({"orchestration_mode": "relay", "intake_session_id": "tis-1"})
        svc.set_node_status("t1", "bbs-step", Status.PENDING)
        routes = importlib.import_module("agentclaw.community.adapters.http.task.relay_routes")

        async def verified_bot(_request):
            return SimpleNamespace(bot=SimpleNamespace(bot_uuid="bot-1:owner-1"))

        monkeypatch.setattr(routes, "require_task_bot", verified_bot)
        response = client.post(
            "/api/v1/collaboration/tasks/bbs/claim",
            json={"task_id": "t1", "node_id": "bbs-step", "bot_id": "another-bot"},
        )
        assert response.status_code != 200
        assert svc.callback.calls == []

    def test_new_relay_report_requires_current_execution_generation(
        self, task_client, monkeypatch,
    ):
        client, svc = task_client
        svc.set_execution_config({"orchestration_mode": "relay", "intake_session_id": "tis-1"})
        svc.set_node_status("t1", "t1", Status.RUNNING)
        router_module = importlib.import_module(
            "agentclaw.community.adapters.http.task.router"
        )

        async def verified_bot(_request):
            return SimpleNamespace(bot=SimpleNamespace(bot_uuid="bot-1:owner-1"))

        monkeypatch.setattr(router_module, "require_task_bot", verified_bot)
        monkeypatch.setattr(router_module, "require_node_assignee", lambda *_: None)
        body = {
            "task_id": "t1", "node_id": "t1", "event_type": "EXECUTION_RESULT",
            "event_id": "event-1", "relay_token": "run-1",
            "event": {"execution_decision": "DECLINED"},
        }
        response = client.post("/api/v1/collaboration/tasks/callback/report", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["status"] == "APPLIED"
        assert svc.callback.calls[-1][1].event_id == "event-1"

        rejected = client.post(
            "/api/v1/collaboration/tasks/callback/report",
            json={**body, "event_id": "event-2", "relay_token": "old-run"},
        )
        assert rejected.status_code != 200
        assert len([item for item in svc.callback.calls if item[0] == "new_relay_report"]) == 1

    def test_intake_uses_authenticated_owner_and_server_session_header(self, human_task_client):
        client, svc = human_task_client
        response = client.post(
            "/api/v1/collaboration/tasks/intake",
            headers={"X-Task-Intake-Session-ID": "tis-1"},
            json={"action": "CONTINUE", "message": "补充验收"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["session_id"] == "tis-1"
        _, (command, context) = svc.callback.calls[0]
        assert command.action.value == "CONTINUE"
        assert context.session_id == "tis-1"
        assert context.owner_user_id == "owner-1"

    def test_intake_accepts_mode_and_a2a_proxy_request(self, human_task_client):
        client, svc = human_task_client
        response = client.post(
            "/api/v1/collaboration/tasks/intake",
            json={
                "action": "START",
                "message": "分析支付失败率",
                "requested_a2a_proxy_bot_ref": "proxy",
                "requested_target": {"target_type": "BOT", "target_id": "risk:owner-2"},
                "requested_mode": "centralized",
            },
        )
        assert response.status_code == 200, response.text
        _, (command, context) = svc.callback.calls[0]
        assert command.requested_a2a_proxy_bot_ref == "proxy"
        assert command.requested_target.target_id == "risk:owner-2"
        assert command.requested_mode == TaskExecutionMode.CENTRALIZED
        assert context.source_type == TaskSourceType.API

    def test_intake_exposes_processing_without_run_credentials(self, human_task_client):
        client, svc = human_task_client
        result = svc._confirmed_intake()
        result.status = TaskIntakeStatus.CLARIFYING
        result.a2a_run_id = "run-secret"
        svc.intake_result = result
        response = client.post(
            "/api/v1/collaboration/tasks/intake",
            json={"action": "RESUME"},
            headers={"X-Task-Intake-Session-ID": "tis-1"},
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["processing"] is True
        assert "a2a_run_id" not in data

    def test_start_uses_only_confirmed_session_to_build_request(self, human_task_client):
        client, svc = human_task_client
        response = client.post(
            "/api/v1/collaboration/tasks/start", json={"session_id": "tis-1"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["task_id"] == "task-1"
        assert response.json()["data"]["node_id"] == "task-1"
        assert svc.callback.calls[0] == ("get_confirmed_intake", ("tis-1", "owner-1"))
        _, request = svc.callback.calls[1]
        assert request.owner_user_id == "owner-1"
        assert request.task_spec.goal.objective == "上线模型"
        assert request.execution_config["intake_session_id"] == "tis-1"

    def test_start_rejects_client_supplied_task_fields(self, human_task_client):
        client, svc = human_task_client
        response = client.post(
            "/api/v1/collaboration/tasks/start",
            json={"session_id": "tis-1", "owner_user_id": "attacker"},
        )
        assert response.status_code == 422
        assert svc.callback.calls == []

    def test_get_task_returns_dashboard(self, human_task_client):
        client, svc = human_task_client
        svc.set_node_status("task-1", "task-1", Status.RUNNING)
        response = client.get("/api/v1/collaboration/tasks/task-1")
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["tasks"][0]["task_id"] == "task-1"

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
                "acceptance_result": {"verdict": "DONE", "done_items": [], "gap_items": []},
                "extend_props": {},
            },
        )
        assert response.status_code == 409, response.text

    def test_task_level_legacy_callback_rejected_for_relay_task(self, client):
        client, svc = client
        svc.set_execution_config({"orchestration_mode": "relay"})
        response = client.post(
            "/api/v1/collaboration/tasks/callback/workflow_result",
            json=_body(task_id="t1", status="COMPLETED", is_success=True),
        )
        assert response.status_code == 409, response.text
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
