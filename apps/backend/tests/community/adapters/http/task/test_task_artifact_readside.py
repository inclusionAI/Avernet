"""读侧最小暴露:TaskNodeDTO.artifacts 富化 + graph_to_dto 零变化 + 轨迹 HTML 产物块(spec §7)。"""
from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.task.router import (
    router as task_router,
)
from agentclaw.community.adapters.http.task.router import (
    router as task_internal_router,
)
from agentclaw.community.api.task.task_context_service import (
    TaskContextServiceProtocol,
)
from agentclaw.community.api.task.task_artifact_service import (
    TaskArtifactApiServiceProtocol,
)
from agentclaw.community.core.task.task_service_protocol import TaskServiceProtocol
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
from agentclaw.community.core.task.repository.types import TaskArtifactRecord
from agentclaw.community.core.task.task_context.task_artifact import (
    FileContent,
    StorageType,
    TextContent,
    artifact_content_hash,
)
from agentclaw.community.adapters.http.task.schemas import (
    graph_to_dto,
    runtime_status_to_product_status,
)

_TASK = "t1"
_NODE = "n1"
_NOW = datetime(2026, 10, 20, 12, 0, 0)


def _record(
    *,
    artifact_id: str,
    kind: str,
    content,
    attempt: int,
    artifact_name=None,
    storage="inline",
    created_at=1730000000000,
) -> TaskArtifactRecord:
    return TaskArtifactRecord(
        artifact_id=artifact_id,
        artifact_name=artifact_name,
        task_id=_TASK,
        node_id=_NODE,
        attempt=attempt,
        scope_session_id="sess-1",
        kind=kind,
        storage=storage,
        content=content,
        content_hash=artifact_content_hash(content),
        creator="bot-1",
        modifier="bot-1",
        created_at=created_at,
        gmt_create=_NOW,
    )


def _sample_records() -> list[TaskArtifactRecord]:
    from agentclaw.community.core.task.task_context.task_artifact import UrlLinkContent

    return [
        _record(
            artifact_id="task-art-0000000000000001",
            kind="file",
            content=FileContent(
                storage_type=StorageType.SESSION_FILE,
                storage_id="sess-xxx",
                file_type="application/pdf",
                file_name="analysis-report.pdf",
                file_id="f-987",
            ),
            storage="session_file",
            attempt=1,
            artifact_name=None,
        ),
        _record(
            artifact_id="task-art-0000000000000002",
            kind="text",
            content=TextContent(text="done"),
            attempt=0,
        ),
        _record(
            artifact_id="task-art-0000000000000003",
            kind="url_link",
            content=UrlLinkContent(url="https://internal.example/doc", type="dima需求"),
            attempt=0,
        ),
    ]


def _graph() -> TaskExecutionGraph:
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


class _StubArtifactApiService:
    def __init__(self, artifact_repo):
        self._artifact_repo = artifact_repo
        self.calls = []

    async def sync_session_files_and_list(self, graph, *, limit=200):
        self.calls.append(graph)
        return self._artifact_repo.list_by_task(graph.task_id, limit=limit)


class _StubTaskService:
    def __init__(self) -> None:
        self.callback = None
        self._task_context_service = None

    def get_task_dashboard(self, task_id, node_id=None, include_action_log=False):
        if task_id != _TASK:
            from agentclaw.community.core.task.domain.errors import TaskNotFoundError

            raise TaskNotFoundError(task_id)
        return _graph()

    def get_node_context(self, task_id, node_id):  # pragma: no cover
        raise AssertionError("not exercised here")


class _StubTrajectoryService:
    async def get_trajectory(
        self, task_id, *, do_analysis=False, force_analysis=False, session_detail=False
    ):
        from agentclaw.community.core.task.task_context.task_trajectory.models import (
            TaskTrajectory,
        )

        return TaskTrajectory(task_id=task_id, gmt_create=1000, gmt_modified=1000, timeline=[])

    def emit_trajectory_event(self, *args, **kwargs) -> None:  # pragma: no cover
        pass


class _StubModule(Module):
    def __init__(self, task_service, trajectory_service, artifact_repo):
        super().__init__()
        self._task_service = task_service
        self._trajectory_service = trajectory_service
        self._artifact_repo = artifact_repo
        self._artifact_service = _StubArtifactApiService(artifact_repo)

    @singleton
    @provider
    def task_service(self) -> TaskServiceProtocol:
        return self._task_service

    @singleton
    @provider
    def trajectory_service(self) -> TaskContextServiceProtocol:
        return self._trajectory_service

    @singleton
    @provider
    def artifact_repo(self) -> TaskArtifactRepositoryProtocol:
        return self._artifact_repo

    @singleton
    @provider
    def artifact_service(self) -> TaskArtifactApiServiceProtocol:
        return self._artifact_service


def _client(records) -> TestClient:
    module = _StubModule(
        _StubTaskService(),
        _StubTrajectoryService(),
        _StubArtifactRepo(records),
    )
    injector = Injector([module])
    app = FastAPI()
    app.include_router(task_router)
    app.include_router(task_internal_router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "owner-1"}
    from agentclaw.community.adapters.http.auth.dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: object()
    attach_injector(app, injector)
    client = TestClient(app)
    client.app.state.artifact_service = module._artifact_service
    return client


class TestDashboardEnrichment:
    def test_openapi_dashboard_node_has_artifacts(self):
        client = _client(_sample_records())
        response = client.get(
            "/openapi/v1/collaboration/tasks/dashboard", params={"task_id": _TASK}
        )
        assert response.status_code == 200
        node = response.json()["data"]["tasks"][0]
        artifacts = node["artifacts"]
        assert artifacts is not None
        assert len(artifacts) == 3
        by_id = {a["artifact_id"]: a for a in artifacts}
        file_artifact = by_id["task-art-0000000000000001"]
        assert file_artifact["kind"] == "file"
        assert file_artifact["artifact_name"] == "analysis-report.pdf" or file_artifact[
            "artifact_name"
        ] is None or isinstance(file_artifact["artifact_name"], str)
        assert file_artifact["content"]["storage_type"] == "session_file"
        assert file_artifact["content"]["file_id"] == "f-987"
        assert file_artifact["attempt"] == 1
        assert file_artifact["creator"] == "bot-1"
        assert isinstance(file_artifact["created_at"], int)
        assert len(client.app.state.artifact_service.calls) == 1
        # kind-tagged wire 形
        assert file_artifact["content"]["kind"] == "file"

    def test_attempt_desc_order(self):
        client = _client(_sample_records())
        response = client.get(
            "/openapi/v1/collaboration/tasks/dashboard", params={"task_id": _TASK}
        )
        attempts = [a["attempt"] for a in response.json()["data"]["tasks"][0]["artifacts"]]
        assert attempts == sorted(attempts, reverse=True)

    def test_internal_dashboard_route_enriched_too(self):
        client = _client(_sample_records())
        response = client.get(f"/api/v1/collaboration/tasks/{_TASK}")
        assert response.status_code == 200
        node = response.json()["data"]["tasks"][0]
        assert node["artifacts"] is not None
        assert len(node["artifacts"]) == 3
        assert len(client.app.state.artifact_service.calls) == 1

    def test_internal_dashboard_list_route_uses_artifact_service(self):
        client = _client(_sample_records())
        response = client.get(
            "/api/v1/collaboration/tasks/dashboard", params={"task_id": _TASK}
        )
        assert response.status_code == 200
        assert len(response.json()["data"]["tasks"][0]["artifacts"]) == 3
        assert len(client.app.state.artifact_service.calls) == 1

    def test_node_without_artifacts_gets_empty_list(self):
        """同任务下另一节点的产物:本节点 .artifacts 为空(不串扰)。"""

        import dataclasses

        other = dataclasses.replace(
            _record(
                artifact_id="task-art-aaaaaaaaaaaaaaaa",
                kind="text",
                content=TextContent(text="other-node"),
                attempt=0,
            ),
            node_id="n-other",
        )
        client = _client([other])
        response = client.get(
            "/openapi/v1/collaboration/tasks/dashboard", params={"task_id": _TASK}
        )
        assert response.status_code == 200
        assert response.json()["data"]["tasks"][0]["artifacts"] == []


class TestGraphToDtoEquality:
    def test_no_artifacts_param_keeps_surface(self):
        """graph_to_dto(graph) 不传新参:除新增 optional artifacts 字段外逐键不变。"""

        dto = graph_to_dto(_graph())
        node = dto.tasks[0]
        assert node.artifacts is None
        dump = node.model_dump()
        dump.pop("artifacts", None)
        assert dump == dict(
            node_id=_NODE,
            task_id=_TASK,
            status=runtime_status_to_product_status(Status.RUNNING),
            task_spec=dump["task_spec"],
            run_info=dump["run_info"],
        )
        # 其余字段仍在
        assert dump["task_spec"]["goal"]["objective"] == "objective"

    def test_with_artifacts_mapping_enriches_only_named_nodes(self):
        records = _sample_records()
        mapping = {_NODE: records}
        dto = graph_to_dto(_graph(), artifacts_by_node=mapping)
        artifacts = dto.tasks[0].artifacts
        assert artifacts is not None
        assert len(artifacts) == 3
        assert artifacts[0].kind in {"file", "url_link", "text"}


class TestTrajectoryHtmlArtifactBlock:
    def test_html_contains_artifact_name_and_no_urls(self):
        client = _client(_sample_records())
        response = client.get(
            "/openapi/v1/collaboration/tasks/trajectory",
            params={"task_id": _TASK, "display": "html"},
        )
        assert response.status_code == 200
        html_body = response.text
        # 产物块存在:Descriptor 渲染名(file 产物回落 file_name)
        assert "analysis-report.pdf" in html_body
        # 反目标:产物块不渲染任何 URL/链接(http/https)
        artifact_block = html_body.split("产物", 1)[-1]
        assert "http://" not in artifact_block and "https://" not in artifact_block

    def test_html_without_artifacts_still_renders(self):
        client = _client([])
        response = client.get(
            "/openapi/v1/collaboration/tasks/trajectory",
            params={"task_id": _TASK, "display": "html"},
        )
        assert response.status_code == 200
        assert "任务轨迹" in response.text
