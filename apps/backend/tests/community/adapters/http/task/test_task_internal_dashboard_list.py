"""内部面镜像:GET /api/v1/collaboration/tasks/dashboard 与 /list(/openapi/v1 同逻辑,改其一须同步)。"""
from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.task.router import router as task_internal_router
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.core.repository.protocols.task import (
    TaskArtifactRepositoryProtocol,
)
from agentclaw.community.core.task.domain.models import (
    Context,
    Goal,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.repository.types import TaskArtifactRecord, TaskInfoRecord
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService  # noqa: F401
from agentclaw.community.core.task.task_service_protocol import TaskServiceProtocol

_TASK = "t1"
_NODE = "n1"


def _graph():
    node = TaskNode(
        node_id=_NODE,
        task_id=_TASK,
        status=Status.RUNNING,
        task_spec=TaskSpec(
            context=Context(title="title", background="background"),
            goal=Goal(objective="objective", acceptances=[]),
        ),
        run_info=RuntimeInfo(assignee="bot-1"),
        node_run_graph=None,
    )
    graph = TaskExecutionGraph(
        run_id=1,
        loop_round=0,
        status=Status.RUNNING,
        output={},
        extend_props={},
        tasks=[node],
        task_id=_TASK,
    )
    node.node_run_graph = graph
    return graph


def _artifact_record(artifact_id="task-art-0000000000000001"):
    from agentclaw.community.core.task.task_context.task_artifact import (
        TextContent,
        artifact_content_hash,
    )

    return TaskArtifactRecord(
        artifact_id=artifact_id,
        artifact_name=None,
        task_id=_TASK,
        node_id=_NODE,
        attempt=0,
        scope_session_id=None,
        kind="text",
        storage="inline",
        content=TextContent(text="done"),
        content_hash=artifact_content_hash(TextContent(text="done")),
        creator="bot-1",
        modifier="bot-1",
        created_at=1730000000000,
        gmt_create=datetime(2026, 10, 20, 12, 0, 0),
    )


class _StubArtifactRepo:
    def __init__(self, records):
        self._records = records

    def list_by_task(self, task_id, *, limit=200):
        return [r for r in self._records if r.task_id == task_id][:limit]

    def list_by_task_node(self, task_id, node_id, *, limit=100):
        return [
            r
            for r in self._records
            if r.task_id == task_id and r.node_id == node_id
        ][:limit]


def _task_record(task_id="T-1", status=Status.RUNNING):
    return TaskInfoRecord(
        id=1,
        task_id=task_id,
        source_type="bot",
        owner_user_id="U-1",
        owner_bot_id="B-1",
        execution_config={},
        task_spec={"metadata": {"task_id": task_id}},
        status=status,
        gmt_create=datetime(2026, 10, 20, 12, 0, 0),
        gmt_modified=datetime(2026, 10, 20, 12, 0, 0),
    )


class _StubTaskService:
    def __init__(self) -> None:
        self.dashboard_calls: list = []
        self.list_calls: list = []
        self.page_calls: list = []

    def get_task_dashboard(self, task_id, node_id=None, include_action_log=False):
        self.dashboard_calls.append((task_id, node_id, include_action_log))
        if task_id != _TASK:
            from agentclaw.community.core.task.domain.errors import TaskNotFoundError

            raise TaskNotFoundError(task_id)
        return _graph()

    def list_tasks(self, status=None, owner_user_id=None):
        self.list_calls.append((status, owner_user_id))
        return [_task_record()]

    def list_tasks_page(self, status=None, owner_user_id=None, page=1, page_size=100):
        self.page_calls.append((status, owner_user_id, page, page_size))
        return [_task_record()], 1


class _StubModule(Module):
    def __init__(self, task_service, artifact_repo):
        super().__init__()
        self._task_service = task_service
        self._artifact_repo = artifact_repo

    @singleton
    @provider
    def task_service(self) -> TaskServiceProtocol:
        return self._task_service

    @singleton
    @provider
    def artifact_repo(self) -> TaskArtifactRepositoryProtocol:
        return self._artifact_repo


def _client(task_service, artifact_repo) -> TestClient:
    injector = Injector([_StubModule(task_service, artifact_repo)])
    app = FastAPI()
    app.include_router(task_internal_router)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="id-1", staffId="owner-1", operatorName="owner"
    )
    attach_injector(app, injector)
    return TestClient(app)


class TestInternalDashboardMirror:
    def test_dashboard_returns_graph_with_artifact_enrichment(self):
        svc = _StubTaskService()
        client = _client(svc, _StubArtifactRepo([_artifact_record()]))
        response = client.get(f"/api/v1/collaboration/tasks/dashboard?task_id={_TASK}")
        assert response.status_code == 200
        data = response.json()["data"]
        node = data["tasks"][0]
        assert node["task_id"] == _TASK
        assert node["artifacts"] is not None and len(node["artifacts"]) == 1
        assert node["artifacts"][0]["artifact_id"] == "task-art-0000000000000001"
        assert svc.dashboard_calls == [(_TASK, None, False)]

    def test_dashboard_passes_node_id_and_action_log_flag(self):
        svc = _StubTaskService()
        client = _client(svc, _StubArtifactRepo([]))
        response = client.get(
            f"/api/v1/collaboration/tasks/dashboard?task_id={_TASK}&node_id={_NODE}&include_action_log=true"
        )
        assert response.status_code == 200
        assert svc.dashboard_calls == [(_TASK, _NODE, True)]

    def test_dashboard_unknown_task_maps_404(self):
        svc = _StubTaskService()
        client = _client(svc, _StubArtifactRepo([]))
        response = client.get("/api/v1/collaboration/tasks/dashboard?task_id=nope")
        assert response.status_code == 404


class TestInternalListMirror:
    def test_list_returns_full_list_filtered_by_user(self):
        svc = _StubTaskService()
        client = _client(svc, _StubArtifactRepo([]))
        response = client.get("/api/v1/collaboration/tasks/list?user_id=U-1")
        assert response.status_code == 200
        data = response.json()["data"]
        assert isinstance(data, list) and len(data) == 1
        assert data[0]["task_id"] == "T-1"
        assert svc.list_calls == [(None, "U-1")]

    def test_list_status_filter_passthrough(self):
        svc = _StubTaskService()
        client = _client(svc, _StubArtifactRepo([]))
        response = client.get(
            "/api/v1/collaboration/tasks/list?user_id=U-1&status=RUNNING,PLANNING"
        )
        assert response.status_code == 200
        assert svc.list_calls == [("RUNNING,PLANNING", "U-1")]

    def test_list_invalid_status_is_400(self):
        svc = _StubTaskService()
        client = _client(svc, _StubArtifactRepo([]))
        response = client.get("/api/v1/collaboration/tasks/list?user_id=U-1&status=BOGUS")
        assert response.status_code == 400

    def test_list_pagination_and_half_pagination_rules(self):
        svc = _StubTaskService()
        client = _client(svc, _StubArtifactRepo([]))
        paged = client.get(
            "/api/v1/collaboration/tasks/list?user_id=U-1&page=1&page_size=10"
        )
        assert paged.status_code == 200
        data = paged.json()["data"]
        assert data["total"] == 1 and len(data["items"]) == 1
        assert svc.page_calls == [(None, "U-1", 1, 10)]
        half = client.get("/api/v1/collaboration/tasks/list?user_id=U-1&page=1")
        assert half.status_code == 400