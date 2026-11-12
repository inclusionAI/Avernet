"""Service API for Todo execution credentials and artifacts."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.todos.artifact_models import (
    TodoArtifactDownload,
    TodoArtifactKind,
    TodoArtifactRecord,
    TodoExecutionCredential,
)


@runtime_checkable
class TodoArtifactServiceProtocol(Protocol):
    def issue_execution(
        self, *, todo_id: str, collab_context_id: str, executor_id: str
    ) -> TodoExecutionCredential: ...

    def bind_run_id(self, *, execution_id: str, run_id: str) -> None: ...

    def report_artifact(
        self,
        *,
        todo_id: str,
        collab_context_id: str,
        execution_id: str,
        execution_token: str,
        artifact_name: str,
        artifact_kind: TodoArtifactKind | str,
        content: dict[str, object],
        is_final_artifact: bool,
    ) -> TodoArtifactRecord: ...

    def upload_file(
        self,
        *,
        todo_id: str,
        collab_context_id: str,
        execution_id: str,
        execution_token: str,
        artifact_name: str | None,
        file_name: str,
        content_type: str | None,
        payload: bytes,
        is_final_artifact: bool,
    ) -> TodoArtifactRecord: ...

    def list_artifacts(
        self, *, todo_id: str, actor_id: str, env: str | None = None
    ) -> tuple[TodoArtifactRecord, ...]: ...

    def read_file(
        self,
        *,
        todo_id: str,
        artifact_id: str,
        actor_id: str,
        env: str | None = None,
    ) -> TodoArtifactDownload: ...
