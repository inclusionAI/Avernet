from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from agentclaw.community.core.task.task_context.task_artifact.artifact_object_storage import (
    TaskArtifactObjectReadResult,
    TaskArtifactObjectReadStatus,
)
from agentclaw.community.core.task.task_context.task_artifact.artifact_object_storage_config import (
    TaskArtifactStorageConfig,
)
from agentclaw.community.core.task.todos.artifact_models import (
    TodoArtifactKind,
    TodoArtifactRecord,
    TodoExecutionRecord,
    TodoExecutionStatus,
)
from agentclaw.community.core.task.todos.errors import (
    TodoArtifactContentError,
    TodoArtifactObjectNotFoundError,
    TodoArtifactStorageError,
    TodoArtifactUploadTooLargeError,
    TodoExecutionAuthError,
)
from agentclaw.community.core.task.todos.models import TodoRecord, TodoStatus
from agentclaw.community.core.task.todos.services.todo_artifact_service import (
    TodoArtifactService,
)

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


def _todo() -> TodoRecord:
    return TodoRecord(
        todo_id="todo_1",
        space_id=7,
        title="Build report",
        description="Produce the final report",
        bot_id="bot_1",
        bot_name="Bot One",
        status=TodoStatus.IN_PROGRESS,
        collab_context_id="context_1",
        created_by="user_1",
        created_at=NOW,
        updated_at=NOW,
    )


class _TodoRepository:
    def get_todo(self, *, todo_id: str) -> TodoRecord:
        assert todo_id == "todo_1"
        return _todo()


class _SpaceAccess:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def require_space_member(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id=kwargs["space_id"]), SimpleNamespace(
            user_id=kwargs["user_id"]
        )


class _ArtifactRepository:
    def __init__(self) -> None:
        self.executions: dict[str, TodoExecutionRecord] = {}
        self.artifacts: dict[str, TodoArtifactRecord] = {}
        self.fail_create_artifact: Exception | None = None

    def create_execution(
        self,
        *,
        todo_id: str,
        collab_context_id: str,
        token_hash: str,
        executor_id: str,
    ) -> TodoExecutionRecord:
        for execution_id, row in tuple(self.executions.items()):
            if row.todo_id == todo_id and row.status is TodoExecutionStatus.ACTIVE:
                self.executions[execution_id] = replace(
                    row, status=TodoExecutionStatus.REPLACED, updated_at=NOW
                )
        execution_id = f"todoexec_{len(self.executions) + 1}"
        row = TodoExecutionRecord(
            execution_id=execution_id,
            todo_id=todo_id,
            collab_context_id=collab_context_id,
            run_id=None,
            token_hash=token_hash,
            status=TodoExecutionStatus.ACTIVE,
            executor_id=executor_id,
            created_at=NOW,
            updated_at=NOW,
        )
        self.executions[execution_id] = row
        return row

    def get_execution(self, *, execution_id: str) -> TodoExecutionRecord:
        return self.executions[execution_id]

    def bind_run_id(self, *, execution_id: str, run_id: str) -> TodoExecutionRecord:
        row = replace(self.executions[execution_id], run_id=run_id, updated_at=NOW)
        self.executions[execution_id] = row
        return row

    def create_artifact(self, **kwargs) -> TodoArtifactRecord:
        if self.fail_create_artifact is not None:
            raise self.fail_create_artifact
        artifact_id = f"todoartifact_{len(self.artifacts) + 1}"
        row = TodoArtifactRecord(
            artifact_id=artifact_id,
            todo_id=kwargs["todo_id"],
            collab_context_id=kwargs["collab_context_id"],
            execution_id=kwargs["execution_id"],
            artifact_name=kwargs["artifact_name"],
            artifact_kind=kwargs["artifact_kind"],
            content=kwargs["content"],
            content_hash=kwargs["content_hash"],
            creator=kwargs["creator"],
            created_at=NOW,
            updated_at=NOW,
        )
        self.artifacts[artifact_id] = row
        return row

    def list_artifacts(self, *, todo_id: str) -> tuple[TodoArtifactRecord, ...]:
        return tuple(row for row in self.artifacts.values() if row.todo_id == todo_id)

    def get_artifact(self, *, todo_id: str, artifact_id: str) -> TodoArtifactRecord:
        row = self.artifacts[artifact_id]
        assert row.todo_id == todo_id
        return row


class _Storage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_result = True
        self.read_status: TaskArtifactObjectReadStatus | None = None

    def put_object(self, key: str, content: bytes) -> bool:
        if not self.put_result:
            return False
        self.objects[key] = content
        return True

    def get_object(self, key: str) -> TaskArtifactObjectReadResult:
        if self.read_status is not None:
            return TaskArtifactObjectReadResult(status=self.read_status)
        content = self.objects.get(key)
        return TaskArtifactObjectReadResult(
            status=(
                TaskArtifactObjectReadStatus.FOUND
                if content is not None
                else TaskArtifactObjectReadStatus.NOT_FOUND
            ),
            content=content,
        )

    def delete_object(self, key: str) -> bool:
        self.objects.pop(key, None)
        return True


def _service(*, max_upload_mb: int = 1):
    artifacts = _ArtifactRepository()
    access = _SpaceAccess()
    storage = _Storage()
    service = TodoArtifactService(
        repository=_TodoRepository(),
        artifact_repository=artifacts,
        space_access=access,
        storage=storage,
        storage_config=TaskArtifactStorageConfig(
            key_prefix="task-artifacts", max_upload_mb=max_upload_mb
        ),
    )
    credential = service.issue_execution(
        todo_id="todo_1", collab_context_id="context_1", executor_id="bot_1"
    )
    return service, artifacts, access, storage, credential


def _report(service: TodoArtifactService, credential, **overrides):
    values: dict[str, Any] = {
        "todo_id": "todo_1",
        "collab_context_id": "context_1",
        "execution_id": credential.execution_id,
        "execution_token": credential.token,
        "artifact_name": "Result",
        "artifact_kind": TodoArtifactKind.TEXT,
        "content": {"text": "done"},
        "is_final_artifact": True,
    }
    values.update(overrides)
    return service.report_artifact(**values)


@pytest.mark.parametrize(
    ("kind", "content", "expected"),
    [
        (TodoArtifactKind.TEXT, {"text": "  done  "}, {"text": "done"}),
        (
            TodoArtifactKind.URL_LINK,
            {"url": "https://example.test/result", "type": "report"},
            {"url": "https://example.test/result", "type": "report"},
        ),
        (
            TodoArtifactKind.CODE,
            {
                "code_base_name": "repo",
                "code_base_address": "https://example.test/repo",
                "pr_link": "https://example.test/repo/pull/1",
                "pr_desc": "implementation",
            },
            {
                "code_base_name": "repo",
                "code_base_address": "https://example.test/repo",
                "pr_link": "https://example.test/repo/pull/1",
                "pr_desc": "implementation",
            },
        ),
    ],
)
def test_report_persists_supported_non_file_artifacts(kind, content, expected):
    service, _, _, _, credential = _service()

    record = _report(
        service, credential, artifact_kind=kind, content=content
    )

    assert record.artifact_kind is kind
    assert record.content == {
        **expected,
        "kind": kind.value,
        "is_final_artifact": True,
    }
    assert record.creator == "bot_1"


def test_file_upload_roundtrip_reuses_object_storage_and_authorizes_reader():
    service, artifacts, access, storage, credential = _service()

    record = service.upload_file(
        todo_id="todo_1",
        collab_context_id="context_1",
        execution_id=credential.execution_id,
        execution_token=credential.token,
        artifact_name=None,
        file_name="../final report.pdf",
        content_type="application/pdf",
        payload=b"pdf-bytes",
        is_final_artifact=True,
    )
    download = service.read_file(
        todo_id="todo_1", artifact_id=record.artifact_id, actor_id="user_1"
    )

    assert record.artifact_name == "final_report.pdf"
    assert record.content["storage_id"].startswith(
        f"task-artifacts/todos/todo_1/{credential.execution_id}/"
    )
    assert storage.objects[record.content["storage_id"]] == b"pdf-bytes"
    assert download.payload == b"pdf-bytes"
    assert artifacts.list_artifacts(todo_id="todo_1") == (record,)
    assert access.calls == [{"space_id": 7, "user_id": "user_1", "env": None}]


def test_new_execution_replaces_old_token():
    service, _, _, _, first = _service()
    service.issue_execution(
        todo_id="todo_1", collab_context_id="context_1", executor_id="bot_1"
    )

    with pytest.raises(TodoExecutionAuthError):
        _report(service, first)


@pytest.mark.parametrize(
    "override",
    [
        {"todo_id": "todo_other"},
        {"collab_context_id": "context_other"},
        {"execution_token": "wrong-token"},
    ],
)
def test_execution_credential_is_bound_to_todo_context_and_token(override):
    service, _, _, _, credential = _service()

    with pytest.raises(TodoExecutionAuthError):
        _report(service, credential, **override)


def test_json_report_rejects_file_and_non_http_url():
    service, _, _, _, credential = _service()

    with pytest.raises(TodoArtifactContentError, match="object-file"):
        _report(service, credential, artifact_kind=TodoArtifactKind.FILE)
    with pytest.raises(TodoArtifactContentError, match=r"http\(s\)"):
        _report(
            service,
            credential,
            artifact_kind=TodoArtifactKind.URL_LINK,
            content={"url": "file:///tmp/report"},
        )


def test_object_write_failure_does_not_persist_artifact():
    service, artifacts, _, storage, credential = _service()
    storage.put_result = False

    with pytest.raises(TodoArtifactStorageError):
        service.upload_file(
            todo_id="todo_1",
            collab_context_id="context_1",
            execution_id=credential.execution_id,
            execution_token=credential.token,
            artifact_name="report",
            file_name="report.txt",
            content_type="text/plain",
            payload=b"content",
            is_final_artifact=False,
        )

    assert artifacts.artifacts == {}
    assert storage.objects == {}


def test_database_failure_after_upload_propagates_and_leaves_unreferenced_object():
    service, artifacts, _, storage, credential = _service()
    artifacts.fail_create_artifact = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        service.upload_file(
            todo_id="todo_1",
            collab_context_id="context_1",
            execution_id=credential.execution_id,
            execution_token=credential.token,
            artifact_name="report",
            file_name="report.txt",
            content_type="text/plain",
            payload=b"content",
            is_final_artifact=False,
        )

    assert artifacts.artifacts == {}
    assert list(storage.objects.values()) == [b"content"]


def test_upload_size_limit_is_enforced_before_object_write():
    service, _, _, storage, credential = _service(max_upload_mb=0)

    with pytest.raises(TodoArtifactUploadTooLargeError):
        service.upload_file(
            todo_id="todo_1",
            collab_context_id="context_1",
            execution_id=credential.execution_id,
            execution_token=credential.token,
            artifact_name="report",
            file_name="report.txt",
            content_type="text/plain",
            payload=b"content",
            is_final_artifact=False,
        )

    assert storage.objects == {}


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (TaskArtifactObjectReadStatus.NOT_FOUND, TodoArtifactObjectNotFoundError),
        (TaskArtifactObjectReadStatus.FAILED, TodoArtifactStorageError),
    ],
)
def test_object_read_statuses_are_mapped_to_domain_errors(status, error_type):
    service, _, _, storage, credential = _service()
    record = service.upload_file(
        todo_id="todo_1",
        collab_context_id="context_1",
        execution_id=credential.execution_id,
        execution_token=credential.token,
        artifact_name="report",
        file_name="report.txt",
        content_type="text/plain",
        payload=b"content",
        is_final_artifact=False,
    )
    storage.read_status = status

    with pytest.raises(error_type):
        service.read_file(
            todo_id="todo_1", artifact_id=record.artifact_id, actor_id="user_1"
        )
