"""产物手动登记 API 服务:add_artifact 归属交叉校验 + compose + 幂等落库 + 读侧直通。"""
from __future__ import annotations

import json

import pytest

from agentclaw.community.core.task.domain.errors import (
    NodeNotFoundError,
    TaskArtifactContentError,
    TaskNotFoundError,
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
from agentclaw.community.core.task.task_context.task_artifact.report_item import (
    parse_artifact_report_item,
)


def _node(node_id="n-1", *, task_id="t-1", assignee="bot-9", harness_retries=None):
    extend_props = {}
    if harness_retries is not None:
        extend_props["harness_retries"] = harness_retries
    return TaskNode(
        node_id=node_id,
        task_id=task_id,
        status=Status.RUNNING,
        task_spec=TaskSpec(
            context=Context(title="title", background="background"),
            goal=Goal(objective="objective", acceptances=[]),
        ),
        run_info=RuntimeInfo(assignee=assignee, extend_props=extend_props),
        node_run_graph=None,
    )


def _graph(*, session_id="sess-graph", attempts=None):
    """单根图;session_id=None 表示图未绑定会话;attempts 覆盖节点 harness_retries。"""

    root = _node("t-1", assignee="bot-root")
    if session_id is not None:
        root.run_info.extend_props["session_id"] = session_id
    child = _node("n-1", harness_retries=(attempts or {}).get("n-1"))
    return TaskExecutionGraph(
        run_id=7,
        loop_round=1,
        status=Status.RUNNING,
        output={},
        extend_props={},
        tasks=[root, child],
        task_id="t-1",
    )


class _StubGraphRepo:
    def __init__(self, graphs):
        self._graphs = graphs

    def load_graph(self, task_id):
        return self._graphs.get(task_id)


class _StubArtifactRepo:
    def __init__(self):
        self.appended = []
        self.rows = []

    def append(self, record):
        self.appended.append(record)
        return record

    def list_by_task(self, task_id, *, limit=200):
        return [r for r in self.rows if r.task_id == task_id][:limit]

    def list_by_task_node(self, task_id, node_id, *, limit=100):
        return [
            r for r in self.rows if r.task_id == task_id and r.node_id == node_id
        ][:limit]


def _raw_item(**overrides) -> dict:
    raw = {
        "artifact_name": None,
        "artifact_kind": "file",
        "session_id": None,
        "task_id": "t-1",
        "node_id": "n-1",
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
    raw.update(overrides)
    return raw


def _service(graphs, artifact_repo=None):
    return TaskArtifactApiService(
        _StubGraphRepo(graphs), artifact_repo or _StubArtifactRepo()
    )


def test_add_artifact_file_happy_path_compose():
    repo = _StubArtifactRepo()
    service = _service({"t-1": _graph()}, repo)
    item = parse_artifact_report_item(
        _raw_item(), report_task_id="t-1", report_node_id="n-1"
    )
    record = service.add_artifact(item)
    assert record is repo.appended[0]
    assert record.task_id == "t-1" and record.node_id == "n-1"
    assert record.attempt == 0 and record.kind == "file"
    assert record.storage == "bot_workspace"
    # name 回落 file_name(§3.6);creator 取节点 assignee;session 图绑定优先
    assert record.artifact_name == "analysis-report.md"
    assert record.creator == "bot-9"
    assert record.scope_session_id == "sess-graph"
    assert record.content_hash and record.artifact_id.startswith("task-art-")


def test_add_artifact_session_falls_back_to_reported_when_graph_unbound():
    repo = _StubArtifactRepo()
    service = _service({"t-1": _graph(session_id=None)}, repo)
    item = parse_artifact_report_item(
        _raw_item(session_id="sess-reported"),
        report_task_id="t-1",
        report_node_id="n-1",
    )
    record = service.add_artifact(item)
    assert record.scope_session_id == "sess-reported"


def test_add_artifact_unknown_task_404():
    item = parse_artifact_report_item(
        _raw_item(), report_task_id="t-1", report_node_id="n-1"
    )
    with pytest.raises(TaskNotFoundError):
        _service({}).add_artifact(item)


def test_add_artifact_unknown_node_404():
    item = parse_artifact_report_item(
        _raw_item(node_id="n-nope"), report_task_id="t-1", report_node_id="n-nope"
    )
    with pytest.raises(NodeNotFoundError):
        _service({"t-1": _graph()}).add_artifact(item)


def test_add_artifact_attempt_mismatch_rejected():
    graph = _graph(attempts={"n-1": 2})
    repo = _StubArtifactRepo()
    service = _service({"t-1": graph}, repo)
    item = parse_artifact_report_item(
        _raw_item(attempt=1), report_task_id="t-1", report_node_id="n-1"
    )
    with pytest.raises(TaskArtifactContentError):
        service.add_artifact(item)
    assert repo.appended == []  # 拒绝即不落库


def test_add_artifact_creator_defaults_to_system():
    graph = _graph()
    graph.tasks[1].run_info.assignee = None
    repo = _StubArtifactRepo()
    service = _service({"t-1": graph}, repo)
    item = parse_artifact_report_item(
        _raw_item(), report_task_id="t-1", report_node_id="n-1"
    )
    record = service.add_artifact(item)
    assert record.creator == "system"


def test_list_artifacts_passthrough_task_and_node():
    repo = _StubArtifactRepo()

    class _Row:
        task_id = "t-1"
        node_id = "n-1"

    repo.rows = [_Row()]
    service = _service({"t-1": _graph()}, repo)
    assert service.list_artifacts("t-1") == repo.rows
    assert service.list_artifacts("t-1", node_id="n-1") == repo.rows
    assert service.list_artifacts("t-1", node_id="n-2") == []
