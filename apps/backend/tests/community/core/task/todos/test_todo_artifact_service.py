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
from agentclaw.community.core.task.todos.collaboration.protocol import (
    TodoCollaborationContext,
    TodoCollaborationError,
    TodoCollaborationEvent,
    TodoCollaborationEventRequest,
)
from agentclaw.community.core.task.todos.errors import (
    TodoArtifactContentError,
    TodoArtifactObjectNotFoundError,
    TodoArtifactStorageError,
    TodoArtifactUploadTooLargeError,
    TodoExecutionAuthError,
    TodoStateConflictError,
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


class _Collaboration:
    def __init__(self) -> None:
        self.context = TodoCollaborationContext(
            collab_context_id="context_1",
            status="in_progress",
            context_version=1,
            current_event_id="event_1",
            events=(),
        )
        self.append_calls: list[TodoCollaborationEventRequest] = []
        self.fail_append_count = 0

    def get_context(
        self, *, collab_context_id: str, actor_id: str
    ) -> TodoCollaborationContext:
        assert collab_context_id == "context_1"
        assert actor_id == "user_1"
        return self.context

    def append_event(
        self, *, collab_context_id: str, request: TodoCollaborationEventRequest
    ) -> TodoCollaborationEvent:
        assert collab_context_id == "context_1"
        self.append_calls.append(request)
        if self.fail_append_count > 0:
            self.fail_append_count -= 1
            raise TodoCollaborationError("temporary conflict")
        event = TodoCollaborationEvent(
            event_id=f"event_{len(self.context.events) + 2}",
            event_seq=len(self.context.events) + 2,
            event_type=request.event_kind,
            actor_id=request.actor_id,
            actor_type=request.actor_type,
            content=str(request.payload.get("summary") or ""),
            execution_id=str(request.payload.get("run_id") or ""),
            context_version=self.context.context_version + 1,
            status=self.context.status,
            artifacts=(),
            created_at=NOW.isoformat(),
            payload=dict(request.payload),
        )
        self.context = replace(
            self.context,
            status=(
                "done"
                if request.event_kind == "task.completed"
                else self.context.status
            ),
            context_version=self.context.context_version + 1,
            current_event_id=event.event_id,
            events=(*self.context.events, event),
        )
        return event


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
        stage_instance_id: str | None = None,
    ) -> TodoExecutionRecord:
        stage_rows = [
            row
            for row in self.executions.values()
            if row.todo_id == todo_id
            and row.stage_instance_id == stage_instance_id
        ]
        generation = max((row.generation for row in stage_rows), default=0) + 1
        execution_id = f"todoexec_{len(self.executions) + 1}"
        for prior_execution_id, row in tuple(self.executions.items()):
            if (
                row.todo_id != todo_id
                or row.stage_instance_id != stage_instance_id
                or not row.is_current
            ):
                continue
            self.executions[prior_execution_id] = replace(
                row,
                status=(
                    TodoExecutionStatus.REPLACED
                    if row.status is TodoExecutionStatus.ACTIVE
                    else row.status
                ),
                is_current=False,
                replaced_by_execution_id=execution_id,
                updated_at=NOW,
            )
        row = TodoExecutionRecord(
            execution_id=execution_id,
            todo_id=todo_id,
            collab_context_id=collab_context_id,
            run_id=None,
            token_hash=token_hash,
            status=TodoExecutionStatus.ACTIVE,
            executor_id=executor_id,
            stage_instance_id=stage_instance_id,
            generation=generation,
            is_current=True,
            created_at=NOW,
            updated_at=NOW,
        )
        self.executions[execution_id] = row
        return row

    def get_execution(self, *, execution_id: str) -> TodoExecutionRecord:
        return self.executions[execution_id]

    def list_executions(self, *, todo_id: str) -> tuple[TodoExecutionRecord, ...]:
        return tuple(row for row in self.executions.values() if row.todo_id == todo_id)

    def get_current_execution(
        self, *, todo_id: str, stage_instance_id: str
    ) -> TodoExecutionRecord:
        return max(
            (
                row
                for row in self.executions.values()
                if row.todo_id == todo_id
                and row.stage_instance_id == stage_instance_id
                and row.is_current
            ),
            key=lambda row: row.generation,
        )

    def bind_run_id(self, *, execution_id: str, run_id: str) -> TodoExecutionRecord:
        row = replace(self.executions[execution_id], run_id=run_id, updated_at=NOW)
        self.executions[execution_id] = row
        return row

    def complete_active_execution(
        self, *, todo_id: str, collab_context_id: str
    ) -> None:
        for execution_id, row in tuple(self.executions.items()):
            if (
                row.todo_id == todo_id
                and row.collab_context_id == collab_context_id
                and row.status is TodoExecutionStatus.ACTIVE
            ):
                self.executions[execution_id] = replace(
                    row,
                    status=TodoExecutionStatus.COMPLETED,
                    updated_at=NOW,
                )

    def create_artifact(self, **kwargs) -> TodoArtifactRecord:
        if self.fail_create_artifact is not None:
            raise self.fail_create_artifact
        for row in self.artifacts.values():
            if (
                row.todo_id == kwargs["todo_id"]
                and row.execution_id == kwargs["execution_id"]
                and row.content_hash == kwargs["content_hash"]
            ):
                return row
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
    collaboration = _Collaboration()
    service = TodoArtifactService(
        repository=_TodoRepository(),
        artifact_repository=artifacts,
        space_access=access,
        collaboration=collaboration,
        storage=storage,
        storage_config=TaskArtifactStorageConfig(
            key_prefix="task-artifacts", max_upload_mb=max_upload_mb
        ),
    )
    credential = service.issue_execution(todo_id="todo_1", executor_id="bot_1")
    return service, artifacts, access, storage, credential, collaboration


def _report(service: TodoArtifactService, credential, **overrides):
    values: dict[str, Any] = {
        "todo_id": "todo_1",
        "todo_execution_id": credential.execution_id,
        "execution_token": credential.token,
        "artifact_name": "Result",
        "artifact_kind": TodoArtifactKind.TEXT,
        "content": {"text": "done"},
        "is_final_artifact": True,
    }
    values.update(overrides)
    return service.report_artifact(**values)


def _complete(service: TodoArtifactService, credential, **overrides):
    values: dict[str, Any] = {
        "todo_id": "todo_1",
        "todo_execution_id": credential.execution_id,
        "execution_token": credential.token,
    }
    values.update(overrides)
    return service.report_completion(**values)


def test_issue_execution_derives_context_from_todo() -> None:
    _, artifacts, _, _, credential, _ = _service()

    execution = artifacts.get_execution(execution_id=credential.execution_id)

    assert execution.todo_id == "todo_1"
    assert execution.collab_context_id == "context_1"


def test_report_completion_appends_task_completed_without_finishing_execution() -> None:
    service, artifacts, _, _, credential, collaboration = _service()
    service.bind_run_id(execution_id=credential.execution_id, run_id="run_complete")

    _complete(service, credential)

    assert collaboration.context.status == "done"
    assert len(collaboration.context.events) == 1
    event = collaboration.context.events[0]
    assert event.event_type == "task.completed"
    assert event.actor_id == "bot_1"
    assert event.actor_type == "bot"
    assert event.payload == {
        "todo_id": "todo_1",
        "todo_execution_id": credential.execution_id,
        "run_id": "run_complete",
    }
    assert (
        artifacts.get_execution(execution_id=credential.execution_id).status
        is TodoExecutionStatus.ACTIVE
    )


def test_report_completion_is_idempotent_for_same_execution() -> None:
    service, _, _, _, credential, collaboration = _service()

    _complete(service, credential)
    _complete(service, credential)

    assert [event.event_type for event in collaboration.context.events] == [
        "task.completed"
    ]


def test_report_completion_persists_custom_completion_content() -> None:
    service, _, _, _, credential, collaboration = _service()

    _complete(
        service,
        credential,
        summary="需求已澄清，验收项已确认。",
        result={"acceptance_items": ["all tests pass"], "pr_url": "https://example.test/pr/1"},
    )

    event = collaboration.context.events[0]
    assert event.content == "需求已澄清，验收项已确认。"
    assert event.payload["summary"] == "需求已澄清，验收项已确认。"
    assert event.payload["result"] == {
        "acceptance_items": ["all tests pass"],
        "pr_url": "https://example.test/pr/1",
    }


def test_report_completion_rejects_invalid_token() -> None:
    service, _, _, _, credential, collaboration = _service()

    with pytest.raises(TodoExecutionAuthError):
        _complete(service, credential, execution_token="wrong-token")

    assert collaboration.context.events == ()


def test_report_completion_rejects_non_running_context_without_matching_event() -> None:
    service, _, _, _, credential, collaboration = _service()
    collaboration.context = replace(collaboration.context, status="done")

    with pytest.raises(TodoStateConflictError, match="not in progress"):
        _complete(service, credential)

    assert collaboration.context.events == ()


@pytest.mark.parametrize(
    ("kind", "content", "expected", "event_summary"),
    [
        (
            TodoArtifactKind.TEXT,
            {"text": "  done  "},
            {"text": "done"},
            "产出文本：done",
        ),
        (
            TodoArtifactKind.URL_LINK,
            {"url": "https://example.test/result", "type": "report"},
            {"url": "https://example.test/result", "type": "report"},
            "产出链接：Result",
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
            "产出代码：repo",
        ),
    ],
)
def test_report_persists_supported_non_file_artifacts(
    kind, content, expected, event_summary
):
    service, _, _, _, credential, collaboration = _service()
    service.bind_run_id(execution_id=credential.execution_id, run_id="run_1")

    record = _report(service, credential, artifact_kind=kind, content=content)

    assert record.artifact_kind is kind
    assert record.content == {
        **expected,
        "kind": kind.value,
        "is_final_artifact": True,
    }
    assert record.creator == "bot_1"
    assert len(collaboration.context.events) == 1
    event = collaboration.context.events[0]
    assert event.event_type == "artifact.reported"
    assert event.content == event_summary
    assert event.actor_id == "bot_1"
    assert event.payload == {
        "artifact_id": record.artifact_id,
        "todo_id": "todo_1",
        "todo_execution_id": credential.execution_id,
        "run_id": "run_1",
        "artifact_kind": kind.value,
        "artifact_name": "Result",
        "artifact_summary": event_summary.split("：", 1)[1],
        "summary": event_summary,
    }


def test_text_event_summary_normalizes_and_truncates_to_twenty_unicode_characters():
    service, _, _, _, credential, collaboration = _service()
    text = "一 二 三 四 五 六 七 八 九 十 十一 十二 十三 十四"

    _report(service, credential, content={"text": text})

    summary = collaboration.context.events[0].payload["artifact_summary"]
    normalized = " ".join(text.split())
    assert summary == f"{normalized[:20]}…"
    assert len(summary.removesuffix("…")) == 20


def test_file_upload_roundtrip_reuses_object_storage_and_authorizes_reader():
    service, artifacts, access, storage, credential, collaboration = _service()
    service.bind_run_id(execution_id=credential.execution_id, run_id="run_file")

    record = service.upload_file(
        todo_id="todo_1",
        todo_execution_id=credential.execution_id,
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
    assert collaboration.context.events[0].content == "产出文件：final_report.pdf"
    assert collaboration.context.events[0].payload["run_id"] == "run_file"
    assert access.calls == [{"space_id": 7, "user_id": "user_1", "env": None}]


def test_file_upload_event_prefers_concise_artifact_name_as_summary():
    service, _, _, _, credential, collaboration = _service()
    service.bind_run_id(execution_id=credential.execution_id, run_id="run_named_file")

    record = service.upload_file(
        todo_id="todo_1",
        todo_execution_id=credential.execution_id,
        execution_token=credential.token,
        artifact_name="AI 发展预测分析",
        file_name="ai_comprehensive_report.csv",
        content_type="text/csv",
        payload=b"csv-bytes",
        is_final_artifact=True,
    )

    event = collaboration.context.events[0]
    assert record.artifact_name == "AI 发展预测分析"
    assert record.content["file_name"] == "ai_comprehensive_report.csv"
    assert event.content == "产出文件：AI 发展预测分析"
    assert event.payload["artifact_name"] == "AI 发展预测分析"


def test_new_execution_replaces_old_token():
    service, _, _, _, first, _ = _service()
    service.issue_execution(todo_id="todo_1", executor_id="bot_1")

    with pytest.raises(TodoExecutionAuthError):
        _report(service, first)


@pytest.mark.parametrize(
    "override",
    [
        {"todo_id": "todo_other"},
        {"execution_token": "wrong-token"},
    ],
)
def test_execution_credential_is_bound_to_todo_and_token(override):
    service, _, _, _, credential, _ = _service()

    with pytest.raises(TodoExecutionAuthError):
        _report(service, credential, **override)


def test_json_report_rejects_file_and_non_http_url():
    service, _, _, _, credential, _ = _service()

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
    service, artifacts, _, storage, credential, collaboration = _service()
    storage.put_result = False

    with pytest.raises(TodoArtifactStorageError):
        service.upload_file(
            todo_id="todo_1",
            todo_execution_id=credential.execution_id,
            execution_token=credential.token,
            artifact_name="report",
            file_name="report.txt",
            content_type="text/plain",
            payload=b"content",
            is_final_artifact=False,
        )

    assert artifacts.artifacts == {}
    assert storage.objects == {}
    assert collaboration.context.events == ()


def test_database_failure_after_upload_propagates_and_leaves_unreferenced_object():
    service, artifacts, _, storage, credential, collaboration = _service()
    artifacts.fail_create_artifact = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        service.upload_file(
            todo_id="todo_1",
            todo_execution_id=credential.execution_id,
            execution_token=credential.token,
            artifact_name="report",
            file_name="report.txt",
            content_type="text/plain",
            payload=b"content",
            is_final_artifact=False,
        )

    assert artifacts.artifacts == {}
    assert list(storage.objects.values()) == [b"content"]
    assert collaboration.context.events == ()


def test_upload_size_limit_is_enforced_before_object_write():
    service, _, _, storage, credential, _ = _service(max_upload_mb=0)

    with pytest.raises(TodoArtifactUploadTooLargeError):
        service.upload_file(
            todo_id="todo_1",
            todo_execution_id=credential.execution_id,
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
    service, _, _, storage, credential, _ = _service()
    record = service.upload_file(
        todo_id="todo_1",
        todo_execution_id=credential.execution_id,
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


def test_completed_execution_token_is_rejected() -> None:
    service, artifacts, _, _, credential, _ = _service()

    service.complete_execution(todo_id="todo_1", collab_context_id="context_1")

    assert (
        artifacts.get_execution(execution_id=credential.execution_id).status
        is TodoExecutionStatus.COMPLETED
    )
    with pytest.raises(TodoExecutionAuthError):
        _report(service, credential)


def test_retry_reuses_artifact_and_does_not_duplicate_reported_event():
    service, artifacts, _, _, credential, collaboration = _service()
    service.bind_run_id(execution_id=credential.execution_id, run_id="run_retry")

    first = _report(service, credential)
    second = _report(service, credential)

    assert second.artifact_id == first.artifact_id
    assert len(artifacts.artifacts) == 1
    assert [event.payload["artifact_id"] for event in collaboration.context.events] == [
        first.artifact_id
    ]


def test_retry_heals_artifact_persisted_before_transient_event_failure():
    service, artifacts, _, _, credential, collaboration = _service()
    service.bind_run_id(execution_id=credential.execution_id, run_id="run_retry")
    collaboration.fail_append_count = 3

    with pytest.raises(TodoCollaborationError, match="temporary conflict"):
        _report(service, credential)

    assert len(artifacts.artifacts) == 1
    assert collaboration.context.events == ()
    collaboration.fail_append_count = 0

    record = _report(service, credential)

    assert len(artifacts.artifacts) == 1
    assert collaboration.context.events[0].payload["artifact_id"] == record.artifact_id


def test_superseded_execution_token_is_rejected_even_if_status_is_stale_active() -> None:
    service, artifacts, _, _, credential, _ = _service()
    execution = artifacts.get_execution(execution_id=credential.execution_id)
    artifacts.executions[credential.execution_id] = replace(
        execution,
        status=TodoExecutionStatus.ACTIVE,
        is_current=False,
    )

    with pytest.raises(TodoExecutionAuthError):
        _report(service, credential)
