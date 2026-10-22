"""产物 API 路由契约:POST /artifacts(手动登记)+ GET /artifacts(按 task_id 列产物)。

端到端走真实编排(真实 ``TaskArtifactApiService`` + 真实 parse),仓储以
带 select-first 查重语义的替身注入;域错误 → 404/422 信封映射见 responses 表。
"""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.task.router import router as task_internal_router
from agentclaw.community.api.task.task_artifact_service import (
    TaskArtifactApiServiceProtocol,
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
from agentclaw.community.core.task.task_context.task_artifact.artifact_api_service import (
    TaskArtifactApiService,
)

_TASK = "t1"
_NODE = "n1"
OTHER_NODE = "n2"


def _graph():
    root = TaskNode(
        node_id=_TASK,
        task_id=_TASK,
        status=Status.RUNNING,
        task_spec=TaskSpec(
            context=Context(title="title", background="background"),
            goal=Goal(objective="objective", acceptances=[]),
        ),
        run_info=RuntimeInfo(assignee="bot-root"),
        node_run_graph=None,
    )
    root.run_info.extend_props["session_id"] = "sess-graph"
    node = TaskNode(
        node_id=_NODE,
        task_id=_TASK,
        status=Status.RUNNING,
        task_spec=TaskSpec(
            context=Context(title="title", background="background"),
            goal=Goal(objective="objective", acceptances=[]),
        ),
        run_info=RuntimeInfo(assignee="bot-exec"),
        node_run_graph=None,
    )
    graph = TaskExecutionGraph(
        run_id=1,
        loop_round=0,
        status=Status.RUNNING,
        output={},
        extend_props={},
        tasks=[root, node],
        task_id=_TASK,
    )
    root.node_run_graph = graph
    node.node_run_graph = graph
    return graph


class _StubGraphRepo:
    def load_graph(self, task_id):
        return _graph() if task_id == _TASK else None


class _StubArtifactRepo:
    """带 select-first 查重语义的仓储替身(键同 §5.3:五行组)。"""

    def __init__(self):
        self.rows = []

    def append(self, record):
        for existing in self.rows:
            if (
                existing.task_id == record.task_id
                and existing.node_id == record.node_id
                and existing.attempt == record.attempt
                and existing.kind == record.kind
                and existing.content_hash == record.content_hash
            ):
                return existing
        self.rows.append(record)
        return record

    def list_by_task(self, task_id, *, limit=200):
        return [r for r in self.rows if r.task_id == task_id][:limit]

    def list_by_task_node(self, task_id, node_id, *, limit=100):
        return [
            r for r in self.rows if r.task_id == task_id and r.node_id == node_id
        ][:limit]


class _StubModule(Module):
    @singleton
    @provider
    def artifact_api_service(self) -> TaskArtifactApiServiceProtocol:
        return TaskArtifactApiService(_StubGraphRepo(), _StubArtifactRepo())


def _client() -> tuple[TestClient, _StubArtifactRepo]:
    repo = _StubArtifactRepo()
    injector = Injector([_StubModule()])
    service = injector.get(TaskArtifactApiServiceProtocol)
    app = FastAPI()
    app.include_router(task_internal_router)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="id-1",
        staffId="owner-1",
        operatorName="owner",
    )
    attach_injector(app, injector)
    # 换用共享同一张空表的仓储,让 client 持有替身以便断言落库行
    service._artifact_repo = repo
    return TestClient(app), repo


def _file_body(**overrides) -> dict:
    body = {
        "artifact_name": None,
        "artifact_kind": "file",
        "session_id": None,
        "task_id": _TASK,
        "node_id": _NODE,
        "attempt": 0,
        "text_content": None,
        "file_content": json.dumps(
            {
                "storage_type": "bot_workspace",
                "storage_id": "reports/analysis-report.md",
                "file_type": "text/markdown",
                "file_name": "analysis-report.md",
                "file_id": None,
            }
        ),
        "url_link_content": None,
        "code_content": None,
    }
    body.update(overrides)
    return body


class TestAddArtifact:
    def test_post_returns_descriptor(self):
        client, repo = _client()
        response = client.post("/api/v1/collaboration/tasks/artifacts", json=_file_body())
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["artifact_id"].startswith("task-art-")
        # name 回落 file_name;creator 取节点 assignee;session 图绑定优先
        assert data["artifact_name"] == "analysis-report.md"
        assert data["kind"] == "file"
        assert data["creator"] == "bot-exec"
        assert data["attempt"] == 0
        assert data["content"]["storage_type"] == "bot_workspace"
        assert data["content"]["kind"] == "file"
        assert len(repo.rows) == 1

    def test_post_same_content_twice_is_idempotent(self):
        client, repo = _client()
        first = client.post(
            "/api/v1/collaboration/tasks/artifacts", json=_file_body()
        ).json()["data"]
        second = client.post(
            "/api/v1/collaboration/tasks/artifacts", json=_file_body()
        ).json()["data"]
        assert first["artifact_id"] == second["artifact_id"]
        assert len(repo.rows) == 1

    def test_post_unknown_task_404(self):
        client, _ = _client()
        response = client.post(
            "/api/v1/collaboration/tasks/artifacts", json=_file_body(task_id="t-nope")
        )
        assert response.status_code == 404

    def test_post_unknown_node_404(self):
        client, _ = _client()
        response = client.post(
            "/api/v1/collaboration/tasks/artifacts", json=_file_body(node_id="n-nope")
        )
        assert response.status_code == 404

    def test_post_attempt_mismatch_422(self):
        client, _ = _client()
        response = client.post(
            "/api/v1/collaboration/tasks/artifacts", json=_file_body(attempt=3)
        )
        assert response.status_code == 422

    def test_post_two_content_fields_422(self):
        body = _file_body(text_content=json.dumps({"text": "hi"}))
        client, _ = _client()
        response = client.post("/api/v1/collaboration/tasks/artifacts", json=body)
        assert response.status_code == 422

    def test_post_bad_content_json_422(self):
        body = _file_body(file_content="{not-json")
        client, _ = _client()
        response = client.post("/api/v1/collaboration/tasks/artifacts", json=body)
        assert response.status_code == 422

    def test_post_path_escape_in_file_content_422(self):
        body = _file_body(
            file_content=json.dumps(
                {
                    "storage_type": "bot_workspace",
                    "storage_id": "../../etc/passwd",
                    "file_type": "text/plain",
                    "file_name": "x.txt",
                    "file_id": None,
                }
            )
        )
        client, _ = _client()
        response = client.post("/api/v1/collaboration/tasks/artifacts", json=body)
        assert response.status_code == 422

    def test_post_extra_field_rejected(self):
        client, _ = _client()
        response = client.post(
            "/api/v1/collaboration/tasks/artifacts",
            json=_file_body(unexpected="x"),
        )
        assert response.status_code in (400, 422)


class TestListArtifacts:
    def _seed(self, client, repo):
        client.post("/api/v1/collaboration/tasks/artifacts", json=_file_body())
        client.post(
            "/api/v1/collaboration/tasks/artifacts",
            json=_file_body(
                artifact_kind="text",
                file_content=None,
                text_content=json.dumps({"text": "结论完成"}),
            ),
        )

    def test_get_lists_by_task(self):
        client, repo = _client()
        self._seed(client, repo)
        response = client.get(
            "/api/v1/collaboration/tasks/artifacts", params={"task_id": _TASK}
        )
        assert response.status_code == 200
        items = response.json()["data"]
        assert len(items) == 2
        assert {item["kind"] for item in items} == {"file", "text"}

    def test_get_filters_by_node(self):
        client, repo = _client()
        self._seed(client, repo)
        response = client.get(
            "/api/v1/collaboration/tasks/artifacts",
            params={"task_id": _TASK, "node_id": "n-nope"},
        )
        assert response.status_code == 200
        assert response.json()["data"] == []

    def test_get_limit(self):
        client, repo = _client()
        self._seed(client, repo)
        response = client.get(
            "/api/v1/collaboration/tasks/artifacts",
            params={"task_id": _TASK, "limit": 1},
        )
        assert response.status_code == 200
        assert len(response.json()["data"]) == 1

    def test_get_empty_for_unknown_task(self):
        client, _ = _client()
        response = client.get(
            "/api/v1/collaboration/tasks/artifacts", params={"task_id": "t-unknown"}
        )
        assert response.status_code == 200
        assert response.json()["data"] == []

    def test_get_requires_task_id(self):
        client, _ = _client()
        response = client.get("/api/v1/collaboration/tasks/artifacts")
        assert response.status_code in (400, 422)

    def test_static_route_wins_over_task_id_lookup(self):
        """GET /artifacts 不被 ``/{task_id}`` 动态路由吞掉(guard 名单防御)。"""

        client, _ = _client()
        response = client.get(
            "/api/v1/collaboration/tasks/artifacts", params={"task_id": _TASK}
        )
        assert response.status_code == 200