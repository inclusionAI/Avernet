from __future__ import annotations

import logging
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
from agentclaw.community.api.task.task_artifact_service import (
    TaskArtifactApiServiceProtocol,
)
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

    async def search_bots(self, *, query, task_id, node_id, relay_token):
        self.callback.calls.append(("search_bots", (query, task_id, node_id, relay_token)))
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
    def artifact_service(self) -> TaskArtifactApiServiceProtocol:
        class _EmptyArtifactService:
            def list_artifacts(self, task_id, *, node_id=None, limit=200):
                return []

        return _EmptyArtifactService()

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
