"""Todo execution-token validation and four-kind artifact persistence."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Sequence
from pathlib import PurePath
from urllib.parse import urlparse

from agentclaw.community.core.repository.protocols.task.todo_artifacts import (
    TodoArtifactRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.task.todos import (
    TodoRepositoryProtocol,
)
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
from agentclaw.community.core.task.task_context.task_artifact.artifact_object_storage import (
    TaskArtifactObjectReadStatus,
    TaskArtifactObjectStoragePlugin,
)
from agentclaw.community.core.task.task_context.task_artifact.artifact_object_storage_config import (
    TaskArtifactStorageConfig,
)
from agentclaw.community.core.task.todos.artifact_models import (
    TodoArtifactDownload,
    TodoArtifactKind,
    TodoArtifactRecord,
    TodoExecutionCredential,
    TodoExecutionStatus,
)
from agentclaw.community.core.task.todos.collaboration.protocol import (
    TodoCollaborationError,
    TodoCollaborationEvent,
    TodoCollaborationEventRequest,
    TodoCollaborationPort,
)
from agentclaw.community.core.task.todos.errors import (
    TodoArtifactContentError,
    TodoArtifactObjectNotFoundError,
    TodoArtifactStorageError,
    TodoArtifactStorageNotConfiguredError,
    TodoArtifactUploadTooLargeError,
    TodoExecutionAuthError,
    TodoNotFoundError,
    TodoStateConflictError,
)
from agentclaw.community.core.task.todos.models import TodoStatus

_SAFE_FILE_RE = re.compile(r"[^A-Za-z0-9._-]+")


class TodoArtifactService:
    def __init__(
        self,
        *,
        repository: TodoRepositoryProtocol,
        artifact_repository: TodoArtifactRepositoryProtocol,
        space_access: SpaceAccessServiceProtocol,
        collaboration: TodoCollaborationPort,
        storage: TaskArtifactObjectStoragePlugin | None,
        storage_config: TaskArtifactStorageConfig,
    ) -> None:
        self._repository = repository
        self._artifacts = artifact_repository
        self._space_access = space_access
        self._collaboration = collaboration
        self._storage = storage
        self._config = storage_config

    def issue_execution(
        self, *, todo_id: str, executor_id: str
    ) -> TodoExecutionCredential:
        todo = self._repository.get_todo(todo_id=todo_id)
        if not todo.collab_context_id:
            raise TodoStateConflictError(
                "Todo execution requires a bound collaboration Context"
            )
        token = secrets.token_urlsafe(32)
        row = self._artifacts.create_execution(
            todo_id=todo_id,
            collab_context_id=todo.collab_context_id,
            token_hash=self._token_hash(token),
            executor_id=executor_id,
        )
        return TodoExecutionCredential(execution_id=row.execution_id, token=token)

    def bind_run_id(self, *, execution_id: str, run_id: str) -> None:
        self._artifacts.bind_run_id(execution_id=execution_id, run_id=run_id)

    def complete_execution(self, *, todo_id: str, collab_context_id: str) -> None:
        self._artifacts.complete_active_execution(
            todo_id=todo_id,
            collab_context_id=collab_context_id,
        )

    def report_completion(
        self,
        *,
        todo_id: str,
        todo_execution_id: str,
        execution_token: str,
    ) -> None:
        """Append this Bot execution's completion event.

        The existing scheduler remains the only owner of the A-side Todo
        transition and execution-token invalidation.
        """
        execution = self._authorize(
            todo_id=todo_id,
            execution_id=todo_execution_id,
            token=execution_token,
        )
        todo = self._repository.get_todo(todo_id=todo_id)
        if todo.status is not TodoStatus.IN_PROGRESS:
            raise TodoStateConflictError("Todo completion requires an in-progress Todo")
        if todo.collab_context_id != execution.collab_context_id:
            raise TodoStateConflictError(
                "Todo execution does not match the bound collaboration Context"
            )

        payload: dict[str, object] = {
            "todo_id": todo_id,
            "todo_execution_id": todo_execution_id,
        }
        if execution.run_id:
            payload["run_id"] = execution.run_id

        last_error: TodoCollaborationError | None = None
        for _attempt in range(3):
            context = self._collaboration.get_context(
                collab_context_id=execution.collab_context_id,
                actor_id=todo.created_by,
            )
            if self._has_completion_event(
                context.events,
                todo_execution_id=todo_execution_id,
                run_id=execution.run_id,
            ):
                return
            if context.status != "in_progress":
                raise TodoStateConflictError(
                    "Todo collaboration Context is not in progress"
                )
            try:
                self._collaboration.append_event(
                    collab_context_id=execution.collab_context_id,
                    request=TodoCollaborationEventRequest(
                        expected_version=context.context_version,
                        event_kind="task.completed",
                        primitive_type="Task",
                        actor_id=execution.executor_id,
                        actor_type="bot",
                        parent_event=context.current_event_id,
                        visibility="workspace",
                        payload=payload,
                    ),
                )
                return
            except TodoCollaborationError as exc:
                last_error = exc

        context = self._collaboration.get_context(
            collab_context_id=execution.collab_context_id,
            actor_id=todo.created_by,
        )
        if self._has_completion_event(
            context.events,
            todo_execution_id=todo_execution_id,
            run_id=execution.run_id,
        ):
            return
        assert last_error is not None
        raise last_error

    def report_artifact(
        self,
        *,
        todo_id: str,
        todo_execution_id: str,
        execution_token: str,
        artifact_name: str,
        artifact_kind: TodoArtifactKind | str,
        content: dict[str, object],
        is_final_artifact: bool,
    ) -> TodoArtifactRecord:
        execution = self._authorize(
            todo_id=todo_id,
            execution_id=todo_execution_id,
            token=execution_token,
        )
        kind = self._kind(artifact_kind)
        if kind is TodoArtifactKind.FILE:
            raise TodoArtifactContentError("file artifacts must use object-file upload")
        normalized_name = self._required_text(artifact_name, "artifact_name", 256)
        normalized = self._normalize_content(kind, content)
        normalized["kind"] = kind.value
        normalized["is_final_artifact"] = bool(is_final_artifact)
        return self._create(
            todo_id=todo_id,
            collab_context_id=execution.collab_context_id,
            execution_id=todo_execution_id,
            run_id=execution.run_id,
            artifact_name=normalized_name,
            kind=kind,
            content=normalized,
            creator=execution.executor_id,
        )

    def upload_file(
        self,
        *,
        todo_id: str,
        todo_execution_id: str,
        execution_token: str,
        artifact_name: str | None,
        file_name: str,
        content_type: str | None,
        payload: bytes,
        is_final_artifact: bool,
    ) -> TodoArtifactRecord:
        execution = self._authorize(
            todo_id=todo_id,
            execution_id=todo_execution_id,
            token=execution_token,
        )
        if self._storage is None:
            raise TodoArtifactStorageNotConfiguredError(
                "Todo artifact object storage is not configured"
            )
        if not isinstance(payload, bytes):
            raise TodoArtifactContentError("file payload must be bytes")
        max_bytes = self._config.max_upload_mb * 1024 * 1024
        if len(payload) > max_bytes:
            raise TodoArtifactUploadTooLargeError("Todo artifact upload is too large")
        safe_name = self._safe_file_name(file_name)
        display_name = self._required_text(
            artifact_name or safe_name, "artifact_name", 256
        )
        digest = hashlib.sha256(payload).hexdigest()
        key = "/".join(
            part.strip("/")
            for part in (
                self._config.key_prefix,
                "todos",
                todo_id,
                todo_execution_id,
                f"{digest}-{safe_name}",
            )
            if part.strip("/")
        )
        if not self._storage.put_object(key, payload):
            raise TodoArtifactStorageError("Todo artifact object storage write failed")
        content: dict[str, object] = {
            "kind": TodoArtifactKind.FILE.value,
            "storage_type": "object_file",
            "storage_id": key,
            "file_name": safe_name,
            "file_type": (content_type or "application/octet-stream")[:256],
            "size": len(payload),
            "is_final_artifact": bool(is_final_artifact),
        }
        return self._create(
            todo_id=todo_id,
            collab_context_id=execution.collab_context_id,
            execution_id=todo_execution_id,
            run_id=execution.run_id,
            artifact_name=display_name,
            kind=TodoArtifactKind.FILE,
            content=content,
            creator=execution.executor_id,
        )

    def list_artifacts(
        self, *, todo_id: str, actor_id: str, env: str | None = None
    ) -> tuple[TodoArtifactRecord, ...]:
        self._require_reader(todo_id=todo_id, actor_id=actor_id, env=env)
        return self._artifacts.list_artifacts(todo_id=todo_id)

    def read_file(
        self,
        *,
        todo_id: str,
        artifact_id: str,
        actor_id: str,
        env: str | None = None,
    ) -> TodoArtifactDownload:
        self._require_reader(todo_id=todo_id, actor_id=actor_id, env=env)
        record = self._artifacts.get_artifact(todo_id=todo_id, artifact_id=artifact_id)
        if record.artifact_kind is not TodoArtifactKind.FILE:
            raise TodoArtifactContentError("artifact does not contain file bytes")
        if self._storage is None:
            raise TodoArtifactStorageNotConfiguredError(
                "Todo artifact object storage is not configured"
            )
        storage_id = record.content.get("storage_id")
        if not isinstance(storage_id, str) or not storage_id:
            raise TodoArtifactContentError("file artifact has no storage_id")
        result = self._storage.get_object(storage_id)
        if result.status is TaskArtifactObjectReadStatus.NOT_FOUND:
            raise TodoArtifactObjectNotFoundError("Todo artifact object not found")
        if (
            result.status is TaskArtifactObjectReadStatus.FAILED
            or result.content is None
        ):
            raise TodoArtifactStorageError("Todo artifact object storage read failed")
        return TodoArtifactDownload(record=record, payload=result.content)

    def _authorize(
        self,
        *,
        todo_id: str,
        execution_id: str,
        token: str,
    ):
        try:
            execution = self._artifacts.get_execution(execution_id=execution_id)
        except TodoNotFoundError as exc:
            raise TodoExecutionAuthError("Invalid Todo execution credential") from exc
        valid = (
            execution.status is TodoExecutionStatus.ACTIVE
            and execution.todo_id == todo_id
            and bool(token)
            and hmac.compare_digest(execution.token_hash, self._token_hash(token))
        )
        if not valid:
            raise TodoExecutionAuthError("Invalid Todo execution credential")
        return execution

    def _require_reader(self, *, todo_id: str, actor_id: str, env: str | None) -> None:
        todo = self._repository.get_todo(todo_id=todo_id)
        self._space_access.require_space_member(
            space_id=todo.space_id, user_id=actor_id, env=env
        )

    def _create(
        self,
        *,
        todo_id: str,
        collab_context_id: str,
        execution_id: str,
        run_id: str | None,
        artifact_name: str,
        kind: TodoArtifactKind,
        content: dict[str, object],
        creator: str,
    ) -> TodoArtifactRecord:
        canonical = json.dumps(
            {"artifact_name": artifact_name, "content": content},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        record = self._artifacts.create_artifact(
            todo_id=todo_id,
            collab_context_id=collab_context_id,
            execution_id=execution_id,
            artifact_name=artifact_name,
            artifact_kind=kind,
            content=content,
            content_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            creator=creator,
        )
        self._ensure_reported_event(record=record, run_id=run_id)
        return record

    def _ensure_reported_event(
        self, *, record: TodoArtifactRecord, run_id: str | None
    ) -> None:
        """Append the durable artifact event, healing a prior partial write on retry.

        Artifact rows and Context events live in separate stores. The artifact is
        written first so a failed persistence never emits a false output event.
        If the event append fails, the error is propagated; retrying the same
        report reuses the artifact row and appends the missing event.
        """

        todo = self._repository.get_todo(todo_id=record.todo_id)
        payload = self._artifact_event_payload(record=record, run_id=run_id)
        last_error: TodoCollaborationError | None = None
        for _attempt in range(3):
            context = self._collaboration.get_context(
                collab_context_id=record.collab_context_id,
                actor_id=todo.created_by,
            )
            if any(
                event.event_type == "artifact.reported"
                and event.payload.get("artifact_id") == record.artifact_id
                for event in context.events
            ):
                return
            try:
                self._collaboration.append_event(
                    collab_context_id=record.collab_context_id,
                    request=TodoCollaborationEventRequest(
                        expected_version=context.context_version,
                        event_kind="artifact.reported",
                        primitive_type="Activity",
                        actor_id=record.creator,
                        actor_type="bot",
                        parent_event=context.current_event_id,
                        visibility="workspace",
                        payload=payload,
                    ),
                )
                return
            except TodoCollaborationError as exc:
                last_error = exc
        context = self._collaboration.get_context(
            collab_context_id=record.collab_context_id,
            actor_id=todo.created_by,
        )
        if any(
            event.event_type == "artifact.reported"
            and event.payload.get("artifact_id") == record.artifact_id
            for event in context.events
        ):
            return
        assert last_error is not None
        raise last_error

    @staticmethod
    def _has_completion_event(
        events: Sequence[TodoCollaborationEvent],
        *,
        todo_execution_id: str,
        run_id: str | None,
    ) -> bool:
        for event in events:
            if event.event_type != "task.completed":
                continue
            if event.payload.get("todo_execution_id") == todo_execution_id:
                return True
            if run_id and event.payload.get("run_id") == run_id:
                return True
        return False

    @classmethod
    def _artifact_event_payload(
        cls, *, record: TodoArtifactRecord, run_id: str | None
    ) -> dict[str, object]:
        concise = cls._artifact_summary(record)
        label = {
            TodoArtifactKind.TEXT: "产出文本",
            TodoArtifactKind.FILE: "产出文件",
            TodoArtifactKind.URL_LINK: "产出链接",
            TodoArtifactKind.CODE: "产出代码",
        }[record.artifact_kind]
        return {
            "artifact_id": record.artifact_id,
            "todo_id": record.todo_id,
            "todo_execution_id": record.execution_id,
            "run_id": run_id or "",
            "artifact_kind": record.artifact_kind.value,
            "artifact_name": record.artifact_name,
            "artifact_summary": concise,
            "summary": f"{label}：{concise}",
        }

    @classmethod
    def _artifact_summary(cls, record: TodoArtifactRecord) -> str:
        content = record.content
        if record.artifact_kind is TodoArtifactKind.TEXT:
            text = content.get("text")
            return cls._preview(text if isinstance(text, str) else record.artifact_name)
        if record.artifact_kind is TodoArtifactKind.FILE:
            file_name = content.get("file_name")
            return (
                file_name.strip()
                if isinstance(file_name, str) and file_name.strip()
                else record.artifact_name
            )
        if record.artifact_kind is TodoArtifactKind.URL_LINK:
            return record.artifact_name or str(content.get("url") or "")
        code_name = content.get("code_base_name")
        return (
            code_name.strip()
            if isinstance(code_name, str) and code_name.strip()
            else record.artifact_name
        )

    @staticmethod
    def _preview(value: str, maximum: int = 20) -> str:
        normalized = " ".join(value.split())
        if len(normalized) <= maximum:
            return normalized
        return f"{normalized[:maximum]}…"

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _kind(value: TodoArtifactKind | str) -> TodoArtifactKind:
        try:
            return (
                value
                if isinstance(value, TodoArtifactKind)
                else TodoArtifactKind(value)
            )
        except (TypeError, ValueError) as exc:
            raise TodoArtifactContentError("unsupported Todo artifact kind") from exc

    @classmethod
    def _normalize_content(
        cls, kind: TodoArtifactKind, content: dict[str, object]
    ) -> dict[str, object]:
        if not isinstance(content, dict):
            raise TodoArtifactContentError("artifact content must be an object")
        if kind is TodoArtifactKind.TEXT:
            return {"text": cls._required_text(content.get("text"), "text", 1_000_000)}
        if kind is TodoArtifactKind.URL_LINK:
            url = cls._web_url(content.get("url"), "url")
            result: dict[str, object] = {"url": url}
            link_type = content.get("type")
            if link_type is not None:
                result["type"] = cls._required_text(link_type, "type", 64)
            return result
        if kind is TodoArtifactKind.CODE:
            result = {
                "code_base_name": cls._required_text(
                    content.get("code_base_name"), "code_base_name", 256
                ),
                "code_base_address": cls._web_url(
                    content.get("code_base_address"), "code_base_address"
                ),
            }
            pr_link = content.get("pr_link")
            if pr_link:
                result["pr_link"] = cls._web_url(pr_link, "pr_link")
            pr_desc = content.get("pr_desc")
            if pr_desc is not None:
                result["pr_desc"] = cls._required_text(pr_desc, "pr_desc", 4_000)
            return result
        raise TodoArtifactContentError("file artifacts must use object-file upload")

    @staticmethod
    def _required_text(value: object, field: str, maximum: int) -> str:
        if not isinstance(value, str) or not value.strip():
            raise TodoArtifactContentError(f"{field} is required")
        normalized = value.strip()
        if len(normalized) > maximum:
            raise TodoArtifactContentError(f"{field} is too long")
        return normalized

    @classmethod
    def _web_url(cls, value: object, field: str) -> str:
        normalized = cls._required_text(value, field, 2_048)
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise TodoArtifactContentError(f"{field} must be an http(s) URL")
        return normalized

    @classmethod
    def _safe_file_name(cls, value: str) -> str:
        name = PurePath(value or "artifact.bin").name[:512]
        safe = _SAFE_FILE_RE.sub("_", name).strip("._")
        return safe or "artifact.bin"
