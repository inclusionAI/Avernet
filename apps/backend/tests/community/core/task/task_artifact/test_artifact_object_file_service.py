"""OBJECT_FILE 上传/下载编排服务:key 派生、单向失败安全、逐次校验下载。"""
from __future__ import annotations

import hashlib

import pytest

from agentclaw.community.core.task.domain.errors import (
    NodeNotFoundError,
    TaskStateError,
    TaskArtifactContentError,
    TaskArtifactObjectNotFoundError,
    TaskArtifactStorageError,
    TaskArtifactStorageNotConfiguredError,
    TaskArtifactUploadTooLargeError,
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
from agentclaw.community.core.task.task_context.task_artifact.artifact_object_file_service import (
    TaskArtifactObjectFileService,
)
from agentclaw.community.core.task.task_context.task_artifact.artifact_object_storage_config import (
    TaskArtifactStorageConfig,
)
from agentclaw.community.core.task.task_context.task_artifact.models import (
    FileContent,
    StorageType,
)
from agentclaw.community.core.task.task_context.task_artifact.artifact_object_storage import (
    TaskArtifactObjectReadResult,
    TaskArtifactObjectReadStatus,
)


def _node(node_id="n-1", *, task_id="t-1", harness_retries=2):
    extend_props = {"harness_retries": harness_retries}
    return TaskNode(
        node_id=node_id,
        task_id=task_id,
        status=Status.RUNNING,
        task_spec=TaskSpec(
            context=Context(title="title", background="background"),
            goal=Goal(objective="objective", acceptances=[]),
        ),
        run_info=RuntimeInfo(assignee="bot-9", extend_props=extend_props),
        node_run_graph=None,
    )


def _graph():
    root = _node("t-1")
    root.run_info.extend_props["session_id"] = "sess-graph"
    return TaskExecutionGraph(
        run_id=7,
        loop_round=1,
        status=Status.RUNNING,
        output={},
        extend_props={},
        tasks=[root, _node("n-1")],
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
        self.rows = {}
        self.append_error = None

    def append(self, record):
        if self.append_error is not None:
            raise self.append_error
        self.appended.append(record)
        self.rows.setdefault(record.artifact_id, record)
        return record

    def get_by_artifact_id(self, artifact_id):
        return self.rows.get(artifact_id)

    def first(self):
        return self.appended[0] if self.appended else None


class _StubObjectStorage:
    def __init__(self, *, put=True, read_status=None):
        self.objects: dict[str, bytes] = {}
        self.put_enabled = put
        self.read_status = read_status

    def put_object(self, key, content):
        if not self.put_enabled:
            return False
        self.objects[key] = content
        return True

    def get_object(self, key):
        if self.read_status is not None:
            return TaskArtifactObjectReadResult(self.read_status)
        if key in self.objects:
            return TaskArtifactObjectReadResult(
                TaskArtifactObjectReadStatus.FOUND, self.objects[key]
            )
        return TaskArtifactObjectReadResult(TaskArtifactObjectReadStatus.NOT_FOUND)

    def delete_object(self, key):
        self.objects.pop(key, None)
        return True


def _service(storage=None, *, config=None, repo=None, graph=None):
    return TaskArtifactObjectFileService(
        graph_repo=_StubGraphRepo({"t-1": graph if graph is not None else _graph()}),
        artifact_repo=repo if repo is not None else _StubArtifactRepo(),
        object_storage=storage,
        config=config or TaskArtifactStorageConfig(),
    )


_PAYLOAD = b"%PDF-1.4 object-file-bytes"


# ------------------------------------------------------------------ 上传

def test_upload_happy_path_registers_object_file_row():
    repo = _StubArtifactRepo()
    storage = _StubObjectStorage()
    svc = _service(storage, repo=repo)

    record = svc.upload_object_file(
        task_id="t-1",
        node_id="n-1",
        attempt=2,
        file_name="analysis-report.pdf",
        payload=_PAYLOAD,
        creator="user-123",
    )
    expected_key = (
        f"task-artifacts/t-1/{hashlib.sha256(_PAYLOAD).hexdigest()}.pdf"
    )
    assert record.kind == "file"
    assert record.storage == "object_file"
    assert record.artifact_name == "analysis-report.pdf"
    content = record.content
    assert content.storage_type is StorageType.OBJECT_FILE
    assert content.storage_id == expected_key
    assert content.file_id is None
    assert content.file_type == "application/pdf"
    assert record.creator == "user-123"
    # 字节确实经插件落库。
    assert storage.objects[expected_key] == _PAYLOAD


def test_upload_same_bytes_derives_same_key_same_content():
    repo = _StubArtifactRepo()
    svc = _service(_StubObjectStorage(), repo=repo)
    first = svc.upload_object_file(
        task_id="t-1", node_id="n-1", attempt=2,
        file_name="a.pdf", payload=_PAYLOAD, creator="u",
    )
    second = svc.upload_object_file(
        task_id="t-1", node_id="n-1", attempt=2,
        file_name="a.pdf", payload=_PAYLOAD, creator="u",
    )
    # 内容寻址:同字节 → 同 key → 同 content → 既有 select-first dedupe 收敛为一行。
    assert first.content == second.content
    assert first.content_hash == second.content_hash


def test_upload_put_failure_leaves_no_row():
    repo = _StubArtifactRepo()
    svc = _service(_StubObjectStorage(put=False), repo=repo)

    with pytest.raises(TaskArtifactStorageError):
        svc.upload_object_file(
            task_id="t-1", node_id="n-1", attempt=2,
            file_name="a.pdf", payload=_PAYLOAD, creator="u",
        )
    # 单向失败安全:无对象引用登记。
    assert repo.appended == []


def test_upload_over_cap_raises_413_error_before_any_write():
    config = TaskArtifactStorageConfig(max_upload_mb=1)
    repo = _StubArtifactRepo()
    storage = _StubObjectStorage()
    svc = _service(storage, config=config, repo=repo)

    with pytest.raises(TaskArtifactUploadTooLargeError):
        svc.upload_object_file(
            task_id="t-1", node_id="n-1", attempt=2,
            file_name="big.bin", payload=b"x" * (1024 * 1024 + 1), creator="u",
        )
    assert repo.appended == []
    assert storage.objects == {}


def test_upload_unbound_store_raises_not_configured():
    svc = _service()
    with pytest.raises(TaskArtifactStorageNotConfiguredError):
        svc.upload_object_file(
            task_id="t-1", node_id="n-1", attempt=2,
            file_name="a.pdf", payload=_PAYLOAD, creator="u",
        )


def test_upload_unknown_task_and_node_and_attempt_mismatch():
    svc = _service(_StubObjectStorage())
    with pytest.raises(TaskNotFoundError):
        svc.upload_object_file(
            task_id="missing", node_id="n-1", attempt=2,
            file_name="a.pdf", payload=_PAYLOAD, creator="u",
        )
    with pytest.raises(NodeNotFoundError):
        svc.upload_object_file(
            task_id="t-1", node_id="n-missing", attempt=2,
            file_name="a.pdf", payload=_PAYLOAD, creator="u",
        )
    with pytest.raises(TaskArtifactContentError):
        svc.upload_object_file(
            task_id="t-1", node_id="n-1", attempt=99,
            file_name="a.pdf", payload=_PAYLOAD, creator="u",
        )


def test_upload_content_type_inferred_from_extension():
    repo = _StubArtifactRepo()
    svc = _service(_StubObjectStorage(), repo=repo)
    record = svc.upload_object_file(
        task_id="t-1", node_id="n-1", attempt=2,
        file_name="notes.MD", payload=b"# hi", creator="u",
    )
    assert record.content.file_type == "text/markdown"


# ------------------------------------------------------------------ 下载

def _uploaded(svc):
    return svc.upload_object_file(
        task_id="t-1", node_id="n-1", attempt=2,
        file_name="analysis-report.pdf", payload=_PAYLOAD, creator="u",
    )


def test_download_roundtrip_returns_payload_and_record():
    repo = _StubArtifactRepo()
    svc = _service(_StubObjectStorage(), repo=repo)
    record = _uploaded(svc)

    download = svc.read_object_file(record.artifact_id)
    assert download.payload == _PAYLOAD
    assert download.record.artifact_id == record.artifact_id
    assert download.record.content.file_name == "analysis-report.pdf"


def test_download_unknown_artifact_id_raises_not_found():
    svc = _service(_StubObjectStorage())
    with pytest.raises(TaskArtifactObjectNotFoundError):
        svc.read_object_file("task-art-doesnotexist00")


def test_download_store_missing_object_raises_not_found():
    repo = _StubArtifactRepo()
    svc = _service(_StubObjectStorage(), repo=repo)
    record = _uploaded(svc)

    # 模拟行在、对象丢(留存清理误删等)。
    svc = _service(
        _StubObjectStorage(read_status=TaskArtifactObjectReadStatus.NOT_FOUND),
        repo=repo,
    )
    with pytest.raises(TaskArtifactObjectNotFoundError):
        svc.read_object_file(record.artifact_id)


def test_download_store_failed_raises_storage_error():
    repo = _StubArtifactRepo()
    svc = _service(_StubObjectStorage(), repo=repo)
    record = _uploaded(svc)

    svc = _service(
        _StubObjectStorage(read_status=TaskArtifactObjectReadStatus.FAILED),
        repo=repo,
    )
    with pytest.raises(TaskArtifactStorageError):
        svc.read_object_file(record.artifact_id)


def test_download_unbound_store_raises_not_configured():
    svc = _service()
    with pytest.raises(TaskArtifactStorageNotConfiguredError):
        svc.read_object_file("task-art-anything")


def test_download_non_object_file_row_refused():
    repo = _StubArtifactRepo()
    svc = _service(_StubObjectStorage(), repo=repo)
    record = svc.upload_object_file(
        task_id="t-1", node_id="n-1", attempt=2,
        file_name="a.md", payload=b"doc", creator="u",
    )
    # 构造一行非本介质产物复用同 id。
    from agentclaw.community.core.task.task_context.task_artifact.models import (
        TextContent,
        artifact_content_hash,
    )

    row = repo.rows[record.artifact_id]
    text = TextContent(text="hello")
    repo.rows[record.artifact_id] = type(row)(
        artifact_id=row.artifact_id,
        artifact_name=row.artifact_name,
        task_id=row.task_id,
        node_id=row.node_id,
        attempt=row.attempt,
        scope_session_id=row.scope_session_id,
        kind="text",
        storage="inline",
        content=text,
        content_hash=artifact_content_hash(text),
        creator=row.creator,
        modifier=None,
        created_at=row.created_at,
        gmt_create=None,
    )
    with pytest.raises(TaskArtifactContentError):
        svc.read_object_file(record.artifact_id)


def test_download_key_outside_task_prefix_refused():
    repo = _StubArtifactRepo()
    svc = _service(_StubObjectStorage(), repo=repo)
    record = _uploaded(svc)

    row = repo.rows[record.artifact_id]
    forged = FileContent(
        storage_type=StorageType.OBJECT_FILE,
        storage_id="task-artifacts/other-task/evil.pdf",
        file_type="application/pdf",
        file_name="evil.pdf",
        file_id=None,
    )
    repo.rows[record.artifact_id] = type(row)(
        artifact_id=row.artifact_id,
        artifact_name=row.artifact_name,
        task_id=row.task_id,
        node_id=row.node_id,
        attempt=row.attempt,
        scope_session_id=row.scope_session_id,
        kind=row.kind,
        storage=row.storage,
        content=forged,
        content_hash=row.content_hash,
        creator=row.creator,
        modifier=None,
        created_at=row.created_at,
        gmt_create=None,
    )
    with pytest.raises(TaskArtifactContentError):
        svc.read_object_file(record.artifact_id)

# ------------------------------------------------------------------ relay 分支(spec §14)


def _relay_graph(*, token="gen-1", assignee="bot-9"):
    def factory(node_id, task_id="t-1"):
        node = _node(node_id, task_id=task_id)
        if token is not None:
            node.run_info.extend_props["relay_token"] = token
        node.run_info.assignee = assignee
        return node

    root = factory("t-1")
    root.run_info.extend_props["session_id"] = "sess-graph"
    return TaskExecutionGraph(
        run_id=7,
        loop_round=1,
        status=Status.RUNNING,
        output={},
        extend_props={},
        tasks=[root, factory("n-1")],
        task_id="t-1",
    )


def _relay_service(*, token="gen-1", assignee="bot-9"):
    return _service(
        _StubObjectStorage(),
        repo=_StubArtifactRepo(),
        graph=_relay_graph(token=token, assignee=assignee),
    )


def test_relay_upload_records_assignee_as_creator():
    repo = _StubArtifactRepo()
    storage = _StubObjectStorage()
    svc = _service(
        storage, repo=repo, graph=_relay_graph(token="gen-1", assignee="bot-9")
    )

    record = svc.upload_object_file(
        task_id="t-1",
        node_id="n-1",
        attempt=2,
        file_name="report.pdf",
        payload=_PAYLOAD,
        creator="user-ignored",  # relay 分支忽略 creator 入参
        relay_token="gen-1",
    )
    assert record.creator == "bot-9"
    assert record.storage == "object_file"


def test_relay_upload_rejects_tampered_token_even_when_attempt_mismatched():
    # 校验序钉板(spec §14.5):fence(409)先于 attempt 快照(422)。
    svc = _relay_service(token="gen-1")
    with pytest.raises(TaskStateError, match="attempt has expired"):
        svc.upload_object_file(
            task_id="t-1",
            node_id="n-1",
            attempt=99,  # 故意失配,fence 应先触发
            file_name="report.pdf",
            payload=_PAYLOAD,
            relay_token="gen-2",
        )


def test_relay_upload_rejects_unclaimed_node_and_blank_args():
    unclaimed_svc = _service(
        _StubObjectStorage(),
        repo=_StubArtifactRepo(),
        graph=_relay_graph(token="gen-1", assignee=None),
    )
    with pytest.raises(TaskStateError, match="assignee"):
        unclaimed_svc.upload_object_file(
            task_id="t-1",
            node_id="n-1",
            attempt=2,
            file_name="report.pdf",
            payload=_PAYLOAD,
            relay_token="gen-1",
        )
    with pytest.raises(TaskArtifactContentError):
        _relay_service().upload_object_file(
            task_id="t-1",
            node_id="n-1",
            attempt=2,
            file_name="report.pdf",
            payload=_PAYLOAD,
        )


def test_relay_upload_does_not_relax_attempt_snapshot():
    svc = _relay_service(token="gen-1")
    with pytest.raises(TaskArtifactContentError):
        svc.upload_object_file(
            task_id="t-1",
            node_id="n-1",
            attempt=99,  # 合法 token 仍受快照对齐约束
            file_name="report.pdf",
            payload=_PAYLOAD,
            relay_token="gen-1",
        )
