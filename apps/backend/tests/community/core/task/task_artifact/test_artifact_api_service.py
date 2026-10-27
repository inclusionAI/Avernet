"""产物 API 服务:手动登记及 dashboard session-file 协调。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agentclaw.community.core.task.domain.errors import (
    NodeNotFoundError,
    TaskArtifactContentError,
    TaskNotFoundError,
    TaskStateError,
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
from agentclaw.community.core.task.task_context.task_artifact.artifact_publish import (
    compose_artifact_record,
)
from agentclaw.community.core.task.task_context.task_artifact.models import (
    ArtifactKind,
    FileContent,
    StorageType,
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


class _StubTaskInfoRepo:
    def __init__(self, executor_app=None):
        self.executor_app = executor_app

    def get(self, task_id):
        if self.executor_app is None:
            return None
        return SimpleNamespace(task_id=task_id, executor_app=self.executor_app)


class _StubArtifactRepo:
    def __init__(self, events=None, *, append_error=None):
        self.appended = []
        self.rows = []
        self.events = events if events is not None else []
        self.append_error = append_error
        self.list_calls = 0
        self.list_limits = []

    def append(self, record):
        self.events.append("db:append")
        if self.append_error is not None:
            raise self.append_error
        self.appended.append(record)
        self.rows.append(record)
        return record

    def list_by_task(self, task_id, *, limit=200):
        self.events.append("db:list")
        self.list_calls += 1
        self.list_limits.append(limit)
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


def _service(
    graphs,
    artifact_repo=None,
    *,
    session_files=None,
    bcs_bot_tokens=None,
    task_info_executor_app=None,
):
    return TaskArtifactApiService(
        _StubGraphRepo(graphs),
        artifact_repo or _StubArtifactRepo(),
        session_files=session_files,
        bcs_bot_tokens=bcs_bot_tokens,
        task_info_repo=_StubTaskInfoRepo(task_info_executor_app),
    )


class _StubSessionFiles:
    def __init__(self, items=None, *, events=None, error=None):
        self.items = items or []
        self.events = events if events is not None else []
        self.error = error
        self.calls = []

    async def list_files(self, session_id, **kwargs):
        self.events.append("bcs:list")
        self.calls.append((session_id, kwargs))
        if self.error is not None:
            raise self.error
        offset = kwargs.get("offset", 0)
        limit = kwargs.get("limit", 100)
        return list(self.items[offset : offset + limit])


class _StubBotTokens:
    def __init__(self, tokens=None, *, error=None):
        self.tokens = tokens or {}
        self.error = error
        self.calls = []

    def get_token(self, holder):
        self.calls.append(holder)
        if self.error is not None:
            raise self.error
        return self.tokens.get(holder)


def _ready_file(**overrides):
    item = {
        "file_id": "f-1",
        "file_name": "report.pdf",
        "mime_type": "application/pdf",
        "status": "Ready",
    }
    item.update(overrides)
    return item


def _session_content(**overrides):
    values = {
        "storage_type": StorageType.SESSION_FILE,
        "storage_id": "sess-child",
        "file_type": "application/pdf",
        "file_name": "report.pdf",
        "file_id": "f-1",
    }
    values.update(overrides)
    return FileContent(**values)


def _record(content, *, node_id="n-existing", attempt=9):
    return compose_artifact_record(
        task_id="t-1",
        node_id=node_id,
        attempt=attempt,
        scope_session_id=content.storage_id,
        kind=ArtifactKind.FILE,
        content=content,
        artifact_name=content.file_name,
        creator="bot-existing",
        now_ms=1,
    )


def _session_graph(*, second_node=False):
    graph = _graph(session_id=None, attempts={"n-1": 2})
    child = graph.tasks[1]
    child.run_info.extend_props.update(
        {"session_id": "sess-child", "driver_bot_id": "driver-1"}
    )
    if second_node:
        other = _node("n-2", assignee="bot-2", harness_retries=1)
        other.run_info.extend_props.update(
            {"session_id": "sess-child", "driver_bot_id": "driver-2"}
        )
        graph.tasks.append(other)
    return graph


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


@pytest.mark.asyncio
async def test_dashboard_sync_lists_session_files_before_db_and_refreshes_after_write():
    events = []
    repo = _StubArtifactRepo(events)
    files = _StubSessionFiles([_ready_file()], events=events)
    tokens = _StubBotTokens({"driver-1": "bearer-1"})
    graph = _session_graph()
    service = _service(
        {"t-1": graph},
        repo,
        session_files=files,
        bcs_bot_tokens=tokens,
    )

    rows = await service.sync_session_files_and_list(graph)

    assert events == ["bcs:list", "db:list", "db:append", "db:list"]
    assert repo.list_calls == 2
    assert repo.list_limits == [None, 200]
    assert rows == repo.rows and len(rows) == 1
    record = rows[0]
    assert record.node_id == "n-1"
    assert record.attempt == 2
    assert record.scope_session_id == "sess-child"
    assert record.creator == "bot-9"
    assert record.artifact_name == "report.pdf"
    assert record.content == _session_content()
    assert files.calls == [
        ("sess-child", {"status": "Ready", "caller_bearer": "bearer-1"})
    ]
    assert tokens.calls == ["driver-1"]


@pytest.mark.asyncio
async def test_dashboard_sync_ignores_equal_file_content_task_wide():
    repo = _StubArtifactRepo()
    existing = _record(_session_content(), node_id="another-node", attempt=7)
    repo.rows = [existing]
    files = _StubSessionFiles([_ready_file()])
    graph = _session_graph()
    service = _service({"t-1": graph}, repo, session_files=files)

    rows = await service.sync_session_files_and_list(graph)

    assert rows == [existing]
    assert repo.appended == []
    assert repo.list_calls == 1


@pytest.mark.asyncio
async def test_dashboard_sync_compares_full_manifest_beyond_response_limit():
    repo = _StubArtifactRepo()
    first = _record(_session_content(file_id="f-other"))
    existing = _record(_session_content(), node_id="another-node", attempt=7)
    repo.rows = [first, existing]
    files = _StubSessionFiles([_ready_file()])
    graph = _session_graph()
    service = _service({"t-1": graph}, repo, session_files=files)

    rows = await service.sync_session_files_and_list(graph, limit=1)

    assert rows == [first]
    assert repo.appended == []
    assert repo.list_limits == [None]


@pytest.mark.asyncio
async def test_dashboard_sync_deduplicates_same_content_discovered_from_multiple_nodes():
    repo = _StubArtifactRepo()
    files = _StubSessionFiles([_ready_file()])
    graph = _session_graph(second_node=True)
    service = _service({"t-1": graph}, repo, session_files=files)

    await service.sync_session_files_and_list(graph)

    assert len(files.calls) == 2  # same session, different holder identity
    assert len(repo.appended) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing_content",
    [
        _session_content(file_id="f-other"),
        _session_content(file_name="other.pdf"),
        _session_content(file_type="application/octet-stream"),
        _session_content(storage_id="sess-other"),
    ],
)
async def test_dashboard_sync_full_file_content_difference_is_new(existing_content):
    repo = _StubArtifactRepo()
    repo.rows = [_record(existing_content)]
    files = _StubSessionFiles([_ready_file()])
    graph = _session_graph()
    service = _service({"t-1": graph}, repo, session_files=files)

    await service.sync_session_files_and_list(graph)

    assert len(repo.appended) == 1
    assert repo.appended[0].content == _session_content()


@pytest.mark.asyncio
async def test_dashboard_sync_skips_missing_session_and_non_ready_items():
    repo = _StubArtifactRepo()
    files = _StubSessionFiles(
        [
            _ready_file(status="Pending"),
            _ready_file(file_id=""),
            _ready_file(file_name=""),
        ]
    )
    graph = _graph(session_id=None)
    service = _service({"t-1": graph}, repo, session_files=files)

    assert await service.sync_session_files_and_list(graph) == []
    assert files.calls == []
    assert repo.appended == []

    graph.tasks[1].run_info.extend_props["session_id"] = "sess-child"
    assert await service.sync_session_files_and_list(graph) == []
    assert len(files.calls) == 1
    assert repo.appended == []


@pytest.mark.asyncio
async def test_dashboard_sync_uses_mime_fallback():
    repo = _StubArtifactRepo()
    files = _StubSessionFiles([_ready_file(mime_type="")])
    graph = _session_graph()
    service = _service({"t-1": graph}, repo, session_files=files)

    await service.sync_session_files_and_list(graph)

    assert repo.appended[0].content.file_type == "application/octet-stream"


@pytest.mark.asyncio
async def test_dashboard_sync_reads_all_ready_session_file_pages():
    repo = _StubArtifactRepo()
    files = _StubSessionFiles(
        [
            _ready_file(file_id=f"f-{index}", file_name=f"report-{index}.pdf")
            for index in range(101)
        ]
    )
    graph = _session_graph()
    service = _service({"t-1": graph}, repo, session_files=files)

    await service.sync_session_files_and_list(graph)

    assert len(repo.appended) == 101
    assert files.calls == [
        ("sess-child", {"status": "Ready", "caller_bearer": None}),
        (
            "sess-child",
            {
                "status": "Ready",
                "limit": 100,
                "offset": 100,
                "caller_bearer": None,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_dashboard_sync_token_failure_falls_back_to_hmac_only(caplog):
    repo = _StubArtifactRepo()
    files = _StubSessionFiles([_ready_file()])
    tokens = _StubBotTokens(error=RuntimeError("token unavailable"))
    graph = _session_graph()
    service = _service(
        {"t-1": graph},
        repo,
        session_files=files,
        bcs_bot_tokens=tokens,
    )

    await service.sync_session_files_and_list(graph)

    assert len(repo.appended) == 1
    assert files.calls == [
        ("sess-child", {"status": "Ready", "caller_bearer": None})
    ]
    assert "BCS bot token lookup failed" in caplog.text


@pytest.mark.asyncio
async def test_dashboard_sync_external_failure_degrades_to_database_rows(caplog):
    repo = _StubArtifactRepo()
    existing = _record(_session_content())
    repo.rows = [existing]
    files = _StubSessionFiles(error=RuntimeError("bcs unavailable"))
    graph = _session_graph()
    service = _service({"t-1": graph}, repo, session_files=files)

    rows = await service.sync_session_files_and_list(graph)

    assert rows == [existing]
    assert repo.appended == []
    assert "session file lookup failed" in caplog.text


@pytest.mark.asyncio
async def test_dashboard_sync_append_failure_propagates():
    repo = _StubArtifactRepo(append_error=RuntimeError("db write failed"))
    files = _StubSessionFiles([_ready_file()])
    graph = _session_graph()
    service = _service({"t-1": graph}, repo, session_files=files)

    with pytest.raises(RuntimeError, match="db write failed"):
        await service.sync_session_files_and_list(graph)
    assert repo.list_calls == 1


@pytest.mark.asyncio
async def test_dashboard_sync_without_session_integration_lists_database_once():
    repo = _StubArtifactRepo()
    existing = _record(_session_content())
    repo.rows = [existing]
    graph = _session_graph()
    service = _service({"t-1": graph}, repo)

    assert await service.sync_session_files_and_list(graph) == [existing]
    assert repo.list_calls == 1


@pytest.mark.asyncio
async def test_dashboard_sync_is_read_only_for_cross_executor_app_owner(monkeypatch):
    from agentclaw.community.core.task.task_context.task_artifact import artifact_api_service

    monkeypatch.setattr(
        artifact_api_service, "get_current_executor_app", lambda: "agentclaw"
    )
    repo = _StubArtifactRepo()
    existing = _record(_session_content())
    repo.rows = [existing]
    files = _StubSessionFiles([_ready_file()])
    graph = _session_graph()
    service = _service(
        {"t-1": graph},
        repo,
        session_files=files,
        task_info_executor_app="agentclawengine",
    )

    rows = await service.sync_session_files_and_list(graph)

    assert rows == [existing]
    assert repo.appended == []
    assert files.calls == []


def test_manual_artifact_write_is_rejected_for_cross_executor_app_owner(monkeypatch):
    from agentclaw.community.core.task.task_context.task_artifact import artifact_api_service

    monkeypatch.setattr(
        artifact_api_service, "get_current_executor_app", lambda: "agentclaw"
    )
    repo = _StubArtifactRepo()
    graph = _session_graph()
    service = _service(
        {"t-1": graph},
        repo,
        task_info_executor_app="agentclawengine",
    )

    with pytest.raises(TaskStateError, match="executor app mismatch"):
        service.add_artifact(
            parse_artifact_report_item(
                _raw_item(), report_task_id="t-1", report_node_id="n-1"
            )
        )
    assert repo.appended == []


# ------------------------------------------------------------------ relay 分支(spec §14.2:补录口 fence-only,creator 派生零改动)


def _relay_token_graph():
    graph = _graph()
    graph.tasks[1].run_info.extend_props["relay_token"] = "gen-1"
    return graph


def test_add_artifact_relay_token_happy_path_keeps_creator_semantics():
    repo = _StubArtifactRepo()
    service = _service({"t-1": _relay_token_graph()}, repo)
    item = parse_artifact_report_item(
        _raw_item(), report_task_id="t-1", report_node_id="n-1"
    )
    record = service.add_artifact(item, relay_token="gen-1")
    # creator 派生零改动:维持既有 assignee 兜底语义(§14.2)。
    assert record.creator == "bot-9"
    assert record is repo.appended[0]


def test_add_artifact_relay_token_mismatch_rejected_without_row():
    repo = _StubArtifactRepo()
    service = _service({"t-1": _relay_token_graph()}, repo)
    item = parse_artifact_report_item(
        _raw_item(), report_task_id="t-1", report_node_id="n-1"
    )
    with pytest.raises(TaskStateError, match="attempt has expired"):
        service.add_artifact(item, relay_token="gen-2")
    assert repo.appended == []


def test_add_artifact_relay_token_unclaimed_node_not_rejected():
    # §14.5 勘定:补录口不增设 assignee 空拒绝——合法性 token 打在无人认领的节点上,
    # 仍登记成功,creator 走既有 system 兜底(与上传口的 409 拒绝不对称是定稿行为)。
    graph = _relay_token_graph()
    graph.tasks[1].run_info.assignee = None
    repo = _StubArtifactRepo()
    service = _service({"t-1": graph}, repo)
    item = parse_artifact_report_item(
        _raw_item(), report_task_id="t-1", report_node_id="n-1"
    )
    record = service.add_artifact(item, relay_token="gen-1")
    assert record.creator == "system"
    assert record is repo.appended[0]
