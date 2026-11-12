"""Persistence contract for Todo execution credentials and artifacts."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.todos.artifact_models import (
    TodoArtifactKind,
    TodoArtifactRecord,
    TodoExecutionRecord,
)


@runtime_checkable
class TodoArtifactRepositoryProtocol(Protocol):
    def create_execution(
        self,
        *,
        todo_id: str,
        collab_context_id: str,
        token_hash: str,
        executor_id: str,
    ) -> TodoExecutionRecord: ...

    def get_execution(self, *, execution_id: str) -> TodoExecutionRecord: ...

    def bind_run_id(self, *, execution_id: str, run_id: str) -> TodoExecutionRecord: ...

    def create_artifact(
        self,
        *,
        todo_id: str,
        collab_context_id: str,
        execution_id: str,
        artifact_name: str,
        artifact_kind: TodoArtifactKind,
        content: dict[str, object],
        content_hash: str,
        creator: str,
    ) -> TodoArtifactRecord: ...

    def list_artifacts(self, *, todo_id: str) -> tuple[TodoArtifactRecord, ...]: ...

    def get_artifact(
        self, *, todo_id: str, artifact_id: str
    ) -> TodoArtifactRecord: ...
