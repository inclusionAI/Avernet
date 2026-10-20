from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from fastapi.testclient import TestClient
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.task.auth import (
    CallbackAuthenticator,
    NoopCallbackAuthenticator,
)
from agentclaw.community.adapters.http.task.router import router, task_callback_router
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.repository.protocols.task import (
    TaskArtifactRepositoryProtocol,
)
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.errors import NotFound
from agentclaw.community.core.task.domain.errors import (
    NodeNotFoundError,
    TaskStateError,
)
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    RuntimeInfo,
    Status,
    TaskContext,
    TaskExecutionGraph,
    TaskNode,
    TaskSpec,
    TaskSourceType,
    TaskOpResult,
    BotCandidate,
    TaskReportResult,
    TaskReportStatus,
)
from agentclaw.community.core.task.domain.intake import (
    IntakeTarget,
    IntakeTargetType,
    TaskExecutionMode,
    TaskIntakeResult,
    TaskIntakeStatus,
)
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry,
    InMemoryCallbackCorrelationRegistry,
)
from agentclaw.community.di.modules.infrastructure.community.task_runner_integration import (
    BcsTokenProviderImpl,
)
from agentclaw.community.core.task.task_runner.client.callback_data_enricher import (
    CallbackDataEnricher,
)


class _RecordingTrajEmitter:
    """生产 ``TaskService._task_context_service`` 的替真:记录 emit 调用供断言。"""

    def __init__(self) -> None:
        self.emit_calls: list[tuple] = []

    def emit_trajectory_event(self, task_id, node_id, action_type, **kwargs):
        self.emit_calls.append((task_id, node_id, action_type, kwargs))


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


_EXECUTION_TRACE_ACTION_TYPES = (
    "S1_CONTEXT",
    "S2_GAPS",
    "S3_CAPABILITY",
    "S4A_EXECUTION",
    "S4B_REPORT",
)


def _execution_trace() -> dict:
    return {
        "steps": [
            {
                "action_type": action_type,
                "action_input": (
                    "{\"step\":\"" + action_type + "\",\"smoke_gate\":\"checked\"}"
                ),
                "action_result": f"{{\"status\":\"completed\",\"output\":\"{action_type}\"}}",
                "status_from": "RUNNING",
                "status_to": "RUNNING",
                "boost_reason": "ok",
                "error_msg": None,
            }
            for action_type in _EXECUTION_TRACE_ACTION_TYPES
        ]
    }


def _make_node(task_id, node_id, status) -> TaskNode:
    return TaskNode(
        node_id=node_id,
        task_id=task_id,
        status=status,
        task_spec=TaskSpec(
            context=Context("bg", title="T"),
            goal=Goal("O", [AcceptanceCriteria("a1", "d")]),
        ),
        run_info=RuntimeInfo(),
        node_run_graph=None,  # type: ignore[arg-type]
    )


class _StubService:
    """最小 TaskServiceProtocol stub:router 仅用 .callback 与 get_task_dashboard。"""

    def __init__(self) -> None:
        self.callback = _StubCallback()
        # 生产 TaskService 的轨迹发射口属性名(emit_relay_report_reject_trajectory
        # 经 getattr(svc, "_task_context_service") 查找)
        self._task_context_service = _RecordingTrajEmitter()
        self._node_status: dict[tuple[str, str], Status] = {}
        self._node_planned_by: dict[tuple[str, str], str] = {}
        self._execution_config: dict = {}
        self.intake_result: TaskIntakeResult | None = None

    def set_node_status(self, task_id, node_id, status) -> None:
        self._node_status[(task_id, node_id)] = status

    def set_execution_config(self, execution_config: dict) -> None:
        self._execution_config = execution_config

    def set_node_planned_by(self, task_id, node_id, origin_node_id) -> None:
        self._node_planned_by[(task_id, node_id)] = origin_node_id

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
                planned_by = self._node_planned_by.get((tid, nid))
                if planned_by:
                    node.run_info.extend_props["relay_planned_by"] = planned_by
                g.tasks.append(node)
        return g

    def get_node_context(self, task_id, node_id):
        graph = self.get_task_dashboard(task_id)
        node = next(item for item in graph.tasks if item.node_id == node_id)
        return TaskContext(
            spec=node.task_spec,
            all_done_output=[],
            gaps=[],
            task_id=task_id,
            node_id=node_id,
            root_task_spec=node.task_spec,
            current_task_spec=node.task_spec,
        )

    async def search_bots(self, *, query, task_id, node_id):
        self.callback.calls.append(("search_bots", (query, task_id, node_id)))
        return [
            BotCandidate(
                bot_id="bot-1",
                name="Risk Bot",
                description="risk analysis",
                score=0.91,
            ),
        ]

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
        self.callback.calls.append(
            ("get_confirmed_intake", (session_id, owner_user_id))
        )
        if session_id != "tis-1" or owner_user_id != "owner-1":
            raise TaskStateError("intake owner mismatch")
        return self._confirmed_intake()

    @staticmethod
    def _confirmed_intake():
        return TaskIntakeResult(
            session_id="tis-1",
            owner_user_id="owner-1",
            source_type=TaskSourceType.API,
            status=TaskIntakeStatus.CONFIRMED,
            target=IntakeTarget(IntakeTargetType.BOT, "bot-1"),
            task_spec=TaskSpec(
                context=Context(
                    "",
                    title="上线",
                    extend_props={
                        "deliverables": ["上线记录"],
                        "constraints": [],
                    },
                ),
                goal=Goal("上线模型", [AcceptanceCriteria("ac1", "完成灰度")]),
            ),
            create_time_ms=1,
            update_time_ms=2,
        )

    async def execute(self, request):
        self.callback.calls.append(("execute", request))
        return TaskOpResult(task_id="task-1", node_id="task-1", success=True, run_id=1)

    def claim_bbs_task(self, task_id, bot_id, node_id=None, claim_id=None):
        self.callback.calls.append(("bbs_claim", (task_id, bot_id, node_id, claim_id)))
        return SimpleNamespace(node_id=node_id or task_id)


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
        return CallbackDataEnricher(
            BcsTokenProviderImpl(base_url="http://bcs"), http_client=None
        )

    @singleton
    @provider
    def artifact_repo(self) -> TaskArtifactRepositoryProtocol:
        class _EmptyArtifactRepo:
            def list_by_task(self, task_id, *, limit=200):
                return []

            def list_by_task_node(self, task_id, node_id, *, limit=100):
                return []

        return _EmptyArtifactRepo()


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
        id="id-1",
        staffId="owner-1",
        operatorName="owner",
    )
    attach_injector(app, injector)
    return TestClient(app), svc


def _body(node=False, **kw):
    d = dict(
        task_id="t1",
        workflow_source="bcn",
        workflow_id="w7",
        workflow_instance_id="i1",
        status="COMPLETED",
        is_success=True,
    )
    d.update(kw)
    if node:
        d.setdefault("node_id", "c1")
    return d


class TestRouter:
    def test_new_relay_rejects_legacy_node_write(self, task_client):
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "t1", Status.RUNNING)
        response = client.post(
            "/api/v1/collaboration/tasks/nodes/update",
            json={"task_id": "t1", "node_id": "t1", "status": "SUCCESS"},
        )
        assert response.status_code != 200
        assert svc.callback.calls == []

    def test_relay_context_uses_network_admission_and_execution_generation(
        self, task_client
    ):
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "t1", Status.RUNNING)

        response = client.get(
            "/api/v1/collaboration/tasks/t1/nodes/t1/context",
            headers={"X-Relay-Token": "run-1"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["node_id"] == "t1"

    def test_relay_bbs_claim_trusts_internal_declared_bot_id(self, task_client):
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "bbs-step", Status.PENDING)
        response = client.post(
            "/api/v1/collaboration/tasks/bbs/claim",
            json={"task_id": "t1", "node_id": "bbs-step", "bot_id": "another-bot"},
        )
        assert response.status_code == 200, response.text
        assert svc.callback.calls[-1] == (
            "bbs_claim",
            ("t1", "another-bot", "bbs-step", None),
        )

    def test_relay_search_uses_internal_network_and_generation(self, task_client):
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "t1", Status.RUNNING)

        response = client.post(
            "/api/v1/collaboration/tasks/search",
            json={
                "query": "需要测试能力",
                "task_id": "t1",
                "node_id": "t1",
                "relay_token": "run-1",
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"] == {
            "candidates": [
                {
                    "bot_id": "bot-1",
                    "name": "Risk Bot",
                    "description": "risk analysis",
                    "score": 0.91,
                },
            ],
        }
        assert svc.callback.calls[-1] == (
            "search_bots",
            ("需要测试能力", "t1", "t1"),
        )

    def test_relay_search_rejects_unknown_fields(self, task_client):
        client, svc = task_client
        before = len(svc.callback.calls)
        response = client.post(
            "/api/v1/collaboration/tasks/search",
            json={
                "query": "需要测试能力",
                "task_id": "t1",
                "node_id": "t1",
                "relay_token": "run-1",
                "extra": "x",
            },
        )

        assert response.status_code == 422, response.text
        assert len(svc.callback.calls) == before

    def test_relay_dispatch_uses_internal_network_and_origin_generation(
        self, task_client
    ):
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "origin", Status.RUNNING)
        svc.set_node_status("t1", "target", Status.PENDING)
        svc.set_node_planned_by("t1", "target", "origin")

        response = client.post(
            "/api/v1/collaboration/tasks/dispatch",
            json={
                "task_id": "t1",
                "origin_node_id": "origin",
                "target_node_id": "target",
                "relay_token": "run-1",
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"] == {"ok": True, "node_id": "target"}
        assert svc.callback.calls[-1] == ("dispatch_node", ("t1", "target"))

    def test_relay_dispatch_rejects_unknown_fields(self, task_client):
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "origin", Status.RUNNING)
        svc.set_node_status("t1", "target", Status.PENDING)
        svc.set_node_planned_by("t1", "target", "origin")
        before = len(svc.callback.calls)

        response = client.post(
            "/api/v1/collaboration/tasks/dispatch",
            json={
                "task_id": "t1",
                "origin_node_id": "origin",
                "target_node_id": "target",
                "relay_token": "run-1",
                "extra": "x",
            },
        )

        assert response.status_code == 422, response.text
        assert len(svc.callback.calls) == before

    def test_new_relay_report_requires_current_execution_generation(
        self,
        task_client,
    ):
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "t1", Status.RUNNING)
        body = {
            "task_id": "t1",
            "node_id": "t1",
            "event_type": "EXECUTION_RESULT",
            "event_id": "event-1",
            "relay_token": "run-1",
            "trace": _execution_trace(),
            "event": {"execution_decision": "DECLINED"},
        }
        response = client.post("/api/v1/collaboration/tasks/callback/report", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["status"] == "APPLIED"
        report_calls = [
            item for item in svc.callback.calls if item[0] == "new_relay_report"
        ]
        assert report_calls[-1][1].event_id == "event-1"

        rejected = client.post(
            "/api/v1/collaboration/tasks/callback/report",
            json={**body, "event_id": "event-2", "relay_token": "old-run"},
        )
        assert rejected.status_code != 200
        assert (
            len([item for item in svc.callback.calls if item[0] == "new_relay_report"])
            == 1
        )

    def test_new_relay_report_validation_failure_answers_422(self, task_client):
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        response = client.post(
            "/api/v1/collaboration/tasks/callback/report",
            json={
                "task_id": "t1",
                "node_id": "t1",
                "event_type": "PLAN_RESULT",
                "event_id": "event-1",
                "relay_token": "run-1",
                "event": None,
                "task_acceptance_result": {
                    "verdict": "PASS",
                    "done_items": [],
                    "gap_items": [],
                },
            },
        )

        assert response.status_code == 422, response.text
        body = response.json()
        assert body["code"] == 422000
        # 该用例的 422 来自非法 AcceptanceResult;trace 缺失不再参与判定。
        # 校验失败也落 task_callback 审计:best-effort 字段 + 脱敏原文 + exec_error
        ingests = [c for c in svc.callback.calls if c[0] == "ingest"]
        assert len(ingests) == 1, "422 校验失败仍应落一条 relay 审计行"
        payload = ingests[0][1].data
        assert payload["result"]["success"] is False
        assert "relay_token" not in str(payload["_raw_callback_body"].get("relay_token")) \
            or payload["_raw_callback_body"]["relay_token"] == "***"
        assert payload["result"]["exec_error"]

    def test_new_relay_report_names_missing_steps_and_step_fields(self, task_client):
        """/callback/report relay 分支的 422 必须把问题点到具体字段路径:
        trace.steps 缺失 / 单个 step 缺七字段之一 / action_type 非枚举。"""
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        base = {
            "task_id": "t1", "node_id": "t1", "event_type": "EXECUTION_RESULT",
            "event_id": "event-v", "relay_token": "run-1",
            "event": {"execution_decision": "DECLINED"},
        }
        full_steps = _execution_trace()["steps"]
        full_step = full_steps[0]

        # ① trace 缺 steps → 点名 trace.steps
        r1 = client.post("/api/v1/collaboration/tasks/callback/report",
                        json={**base, "trace": {}})
        assert r1.status_code == 422
        assert "trace.steps" in r1.json()["message"], r1.json()["message"]

        # ② step 缺 boost_reason → 点名 trace.steps[0].boost_reason
        step_missing = {k: v for k, v in full_step.items() if k != "boost_reason"}
        steps_missing = _execution_trace()["steps"]
        steps_missing[0] = step_missing
        r2 = client.post("/api/v1/collaboration/tasks/callback/report",
                        json={**base, "trace": {"steps": steps_missing}})
        assert r2.status_code == 422
        msg2 = r2.json()["message"]
        assert "boost_reason" in msg2 and "steps[0]" in msg2, msg2

        # ③ 空 action_type → 点名 action_type(S 码主事件标识合法值,不再限枚举)
        steps_bad_type = _execution_trace()["steps"]
        steps_bad_type[0] = {**full_step, "action_type": ""}
        r3 = client.post("/api/v1/collaboration/tasks/callback/report",
                        json={**base, "trace": {"steps": steps_bad_type}})
        assert r3.status_code == 422
        msg3 = r3.json()["message"]
        assert "action_type" in msg3, msg3

    def test_new_relay_report_persists_callback_audit_on_success(self, task_client):
        """成功上报:落一条 task_callback 审计行(invoker=relay,run_id=event_id+node_id
        为幂等 upsert 键,orig 原文脱敏 relay_token,result.success=True)。"""
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "t1", Status.RUNNING)
        response = client.post(
            "/api/v1/collaboration/tasks/callback/report",
            json={
                "task_id": "t1",
                "node_id": "t1",
                "event_type": "EXECUTION_RESULT",
                "event_id": "event-1",
                "relay_token": "run-1",
                "trace": _execution_trace(),
                "event": {"execution_decision": "DECLINED"},
            },
        )
        assert response.status_code == 200, response.text

        ingests = [c for c in svc.callback.calls if c[0] == "ingest"]
        assert len(ingests) == 1, "成功上报应恰好落一条审计行"
        payload = ingests[0][1].data
        # 幂等身份:(run_id=event_id, node_id) —— 经 loop_task_id 表达
        assert payload["loop_task_id"] == "event-1::t1"
        assert payload["node_id"] == "t1"
        assert payload["event_id"] == "event-1"
        assert payload["workflow_source"] == "relay"
        assert payload["status"] == "EXECUTION_RESULT"
        assert payload["result"]["success"] is True
        assert payload["result"]["exec_error"] is None
        # 原始报文留底且 token 脱敏
        raw = payload["_raw_callback_body"]
        assert raw["event_id"] == "event-1"
        assert raw["relay_token"] == "***"
        assert payload["result"]["_ext_info"]["task_id"] == "t1"

    def test_new_relay_report_rejection_emits_system_trajectory_row(self, task_client):
        """被拒上报(token 过期)→ 轨迹事件表落一条 system 类型事件:
        action_type=system、error_msg=异常信息、ext_info含完整堆栈与
        phase/event_id;原异常照常外抛(响应非 200)。"""
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "t1", Status.RUNNING)
        # trace 在线上契约可选;拒绝场景走 token 校验,不带 trace 最小化 body
        response = client.post(
            "/api/v1/collaboration/tasks/callback/report",
            json={
                "task_id": "t1",
                "node_id": "t1",
                "event_type": "EXECUTION_RESULT",
                "event_id": "event-stale",
                "relay_token": "stale-run",
                "event": {"execution_decision": "DECLINED"},
            },
        )
        assert response.status_code != 200, "拒绝照常外抛"

        emitter = svc._task_context_service
        assert len(emitter.emit_calls) == 1, "应恰落一条 system 轨迹行"
        task_id, node_id, action_type, kwargs = emitter.emit_calls[0]
        assert (task_id, node_id, action_type) == ("t1", "t1", "system")
        assert "expired" in kwargs["error_msg"], kwargs["error_msg"]
        ext = kwargs["ext_info"]
        assert ext["phase"] == "callback_report"
        assert ext["relay_event_id"] == "event-stale"
        assert "TaskStateError" in ext["stack"], "堆栈应记录异常类型"
        assert "require_relay_attempt" in ext["stack"] or "Traceback" in ext[
            "stack"], f"堆栈应含调用栈: {ext['stack'][:200]}"

    def test_new_relay_report_persists_callback_audit_on_rejection(
        self, task_client
    ):
        """被拒上报(token 过期):审计照落——success=False + exec_error 携带拒绝原因,
        原异常仍按原路径抛出(响应非 200)。"""
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "t1", Status.RUNNING)
        response = client.post(
            "/api/v1/collaboration/tasks/callback/report",
            json={
                "task_id": "t1",
                "node_id": "t1",
                "event_type": "EXECUTION_RESULT",
                "event_id": "event-stale",
                "relay_token": "stale-run",
                "trace": _execution_trace(),
                "event": {"execution_decision": "DECLINED"},
            },
        )
        assert response.status_code != 200, "token 过期应被拒(原异常外抛)"

        ingests = [c for c in svc.callback.calls if c[0] == "ingest"]
        assert len(ingests) == 1, "被拒上报也应落审计行"
        payload = ingests[0][1].data
        assert payload["loop_task_id"] == "event-stale::t1"
        assert payload["result"]["success"] is False
        assert "expired" in str(payload["result"]["exec_error"])
        assert payload["_raw_callback_body"]["relay_token"] == "***"

    def test_relay_report_audit_payload_to_callback_record(self):
        """payload → ``_to_callback_record`` 落库形态:run_id=event_id/node_id 正确、
        invoker=relay、orig 为脱敏原文 JSON、endpoint 不经 HTTP 直接验证翻译层。"""
        from agentclaw.community.adapters.http.task.callback_routes import (
            relay_report_audit_payload,
        )
        from agentclaw.community.adapters.http.task.schemas_relay_report import (
            TaskReportCommandDTO,
        )
        from agentclaw.community.core.task.task_runner.callback_adapter import (
            _to_callback_record,
        )
        import json as _json

        raw = {
            "task_id": "t1",
            "node_id": "n1",
            "event_type": "EXECUTION_RESULT",
            "event_id": "ev-9",
            "relay_token": "secret-token",
            "trace": _execution_trace(),
            "event": {"execution_decision": "DECLINED"},
        }
        command = TaskReportCommandDTO.model_validate(raw).to_domain()
        payload = relay_report_audit_payload(
            raw, command=command, result=TaskReportResult(TaskReportStatus.APPLIED)
        )
        record = _to_callback_record(payload, event_id="ev-9", process_status=None)

        assert record.run_id == "ev-9"
        assert record.node_id == "n1"
        assert record.invoker == "relay"
        assert record.status == "EXECUTION_RESULT"
        assert record.result_success is True
        assert record.event_id == "ev-9"
        assert record.extend_props == {
            "relay_report": True,
            "task_id": "t1",
            "event_type": "EXECUTION_RESULT",
            "execution_decision": "DECLINED",
        }
        # orig = 脱敏原文(secret token 不进库)
        orig = _json.loads(record.orig_callback_data)
        assert orig["relay_token"] == "***"
        assert orig["event_id"] == "ev-9"

    def test_intake_uses_authenticated_owner_and_server_session_header(
        self, human_task_client
    ):
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

    def test_start_uses_only_confirmed_session_to_build_request(
        self, human_task_client
    ):
        client, svc = human_task_client
        response = client.post(
            "/api/v1/collaboration/tasks/start",
            json={"session_id": "tis-1"},
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
                "acceptance_result": {
                    "verdict": "DONE",
                    "done_items": [],
                    "gap_items": [],
                },
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
        r = c.post(
            "/api/v1/collaboration/tasks/callback/workflow_result",
            json=_body(loop_task_id="t1::root1"),
        )
        assert r.status_code == 200, r.text
        assert svc.callback.calls[0][0] == "result"

    def test_node_result_success(self, client):
        c, svc = client
        r = c.post(
            "/api/v1/collaboration/tasks/callback/node_result", json=_body(node=True)
        )
        assert r.status_code == 200, r.text
        assert svc.callback.calls[0][1].data["loop_task_id"] == "t1::c1"

    def test_workflow_start_success(self, client):
        c, svc = client
        r = c.post(
            "/api/v1/collaboration/tasks/callback/workflow_start",
            json=_body(loop_task_id="t1::root1", status="RUNNING"),
        )
        assert r.status_code == 200, r.text
        assert svc.callback.calls[0][0] == "start"

    def test_node_start_success(self, client):
        c, svc = client
        r = c.post(
            "/api/v1/collaboration/tasks/callback/node_start",
            json=_body(node=True, status="RUNNING"),
        )
        assert r.status_code == 200, r.text
        assert svc.callback.calls[0][0] == "start"

    def test_result_idempotent_when_already_terminal(self, client):
        c, svc = client
        svc.callback.report_result = _raise(TaskStateError("DONE->DONE"))
        svc.set_node_status("t1", "root1", Status.DONE)
        r = c.post(
            "/api/v1/collaboration/tasks/callback/workflow_result",
            json=_body(loop_task_id="t1::root1"),
        )
        assert r.status_code == 200, r.text  # 幂等 ack

    def test_result_409_when_illegal(self, client):
        # 非终态重投:report_result 抛 TaskStateError,非幂等 → 不进幂等分支。
        # ``@envelope_errors`` 经 ``ENVELOPE_ERRORS`` 把它映射为 409 ErrorEnvelope,
        # 该映射在 handler 帧内完成,不依赖本 fixture 的中央 handler。
        c, svc = client
        svc.callback.report_result = _raise(TaskStateError("PENDING->DONE"))
        svc.set_node_status("t1", "root1", Status.PENDING)  # 非终态
        r = c.post(
            "/api/v1/collaboration/tasks/callback/workflow_result",
            json=_body(loop_task_id="t1::root1"),
        )
        assert r.status_code == 409, r.text
        assert r.json()["code"] == 409000

    def test_start_409_on_stale(self, client):
        # start 抛 TaskStateError;disposition!=result 不进幂等分支 → 409 ErrorEnvelope。
        c, svc = client
        svc.callback.start_run = _raise(TaskStateError("stale"))
        r = c.post(
            "/api/v1/collaboration/tasks/callback/node_start",
            json=_body(node=True, status="RUNNING"),
        )
        assert r.status_code == 409, r.text
        assert r.json()["code"] == 409000

    def test_not_found_404(self, client):
        # report_result 抛 NodeNotFoundError → ``ENVELOPE_ERRORS`` 映射为 404 ErrorEnvelope。
        c, svc = client
        svc.callback.report_result = _raise(NodeNotFoundError("x"))
        r = c.post(
            "/api/v1/collaboration/tasks/callback/workflow_result",
            json=_body(loop_task_id="t1::root1"),
        )
        assert r.status_code == 404, r.text
        assert r.json()["code"] == 404000

    def test_correlation_error_400(self, client):
        # task 级无回声 + 空 registry → translate 抛 NotFound(core.errors;原计划 CallbackCorrelationError
        # 尚未落地)。未被 @envelope_errors 映射、本 fixture 无中央 handler → 异常经 TestClient 上抛
        # → 断言领域错误上抛。
        c, _ = client
        with pytest.raises(NotFound):
            c.post(
                "/api/v1/collaboration/tasks/callback/workflow_result", json=_body()
            )  # 无 loop_task_id,registry 空

    def test_validation_422(self, client):
        c, _ = client
        r = c.post(
            "/api/v1/collaboration/tasks/callback/node_result", json={"task_id": "t1"}
        )  # 缺必填
        assert r.status_code == 422, r.text

    def test_claw_mind_callback_ingests_only(self, client):
        # ClawMind HttpCallbackPayload → 只落库(ingest),不推进引擎(不走 start/result)
        c, svc = client
        body = {
            "workflow_id": "wf-1",
            "flow_id": "fl-1",
            "status": "succeeded",
            "ext_info": {
                "flow_runs": {"status": "succeeded", "origin_session_id": "S-9"},
                "node_executions": [],
            },
        }
        r = c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=body)
        assert r.status_code == 200, r.text
        assert svc.callback.calls and svc.callback.calls[0][0] == "ingest"
        assert all(k == "ingest" for k, _ in svc.callback.calls)

    def test_bcn_callback_ingests_only(self, client):
        # BCN CloudEvent(已处理事件)→ 只落库(ingest),不推进引擎
        c, svc = client
        evt = {
            "spec_version": "1.0",
            "event_id": "e1",
            "event_type": "state_machine.node.completed",
            "source": "bcs",
            "scope": {"group_id": "g1", "session_id": "s1", "run_id": "r1"},
            "stream": {"key": "k", "sequence": 1},
            "actor": {"type": "bot", "id": "b"},
            "data": {
                "run_id": "r1",
                "node_id": "n1",
                "outcome": "success",
                "output": {"x": 1},
            },
        }
        r = c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=evt)
        assert r.status_code == 200, r.text
        assert svc.callback.calls and svc.callback.calls[0][0] == "ingest"
        assert all(k == "ingest" for k, _ in svc.callback.calls)

    def test_bcn_unhandled_event_acks_without_ingest(self, client):
        c, svc = client
        evt = {
            "spec_version": "1.0",
            "event_id": "e2",
            "event_type": "message.created",
            "source": "bcs",
            "scope": {"group_id": "g1", "session_id": "s1"},
            "stream": {"key": "k", "sequence": 1},
            "actor": {},
            "data": {},
        }
        r = c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=evt)
        assert r.status_code == 200, r.text
        assert svc.callback.calls == []  # 非处理事件:不落库、不推进
