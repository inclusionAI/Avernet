"""Product policy and authorization orchestration for Todo workbench actions."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Iterable

from injector import inject

from agentclaw.community.core.errors import ValidationError
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.task.todos import (
    TodoRepositoryProtocol,
)
from agentclaw.community.core.spaces.models import SpaceRecord
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
from agentclaw.community.core.task.todos.artifact_models import (
    TodoArtifactKind,
    TodoArtifactRecord as TodoPersistedArtifactRecord,
)
from agentclaw.community.core.task.todos.artifact_service_protocol import (
    TodoArtifactServiceProtocol,
)
from agentclaw.community.core.task.todos.bot_scope import (
    require_todo_bot_owner_id,
    resolve_todo_bot,
    todo_bot_name,
)
from agentclaw.community.core.task.todos.errors import (
    TodoNotFoundError,
    TodoStateConflictError,
)
from agentclaw.community.core.task.todos.execution_protocol import (
    TodoExecutionServiceProtocol,
)
from agentclaw.community.core.task.todos.models import (
    MAX_ACTOR_ID_LENGTH,
    MAX_ACTOR_NAME_LENGTH,
    MAX_ACCEPTANCE_ID_LENGTH,
    MAX_ACCEPTANCE_TARGETS,
    MAX_ARTIFACT_ID_LENGTH,
    MAX_BOT_ID_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_EVENT_ID_LENGTH,
    MAX_MESSAGE_LENGTH,
    MAX_TITLE_LENGTH,
    MAX_TODO_ID_LENGTH,
    AcceptanceVerdict,
    TodoAcceptanceTarget,
    TodoAcceptanceTargetKind,
    TodoArtifactRecord as TodoEventArtifactRecord,
    TodoEventRecord,
    TodoPage,
    TodoRecord,
    TodoStatus,
)
from agentclaw.community.core.task.todos.service_protocol import TodoServiceProtocol
from agentclaw.community.plugin_api.todo_collaboration import (
    TodoCollaborationEvent,
    TodoCollaborationEventRequest,
    TodoCollaborationError,
    TodoCollaborationPlugin,
)

_HUMAN_INTERACTION_EVENT = "user_interaction.posted"
_TASK_COMPLETED_EVENT = "task.completed"
_TASK_ACCEPTED_EVENT = "task.accepted"
_TASK_REJECTED_EVENT = "task.rejected"
_INTERACTION_EVENT_TYPES = {
    "task.created",
    _HUMAN_INTERACTION_EVENT,
    _TASK_COMPLETED_EVENT,
    _TASK_REJECTED_EVENT,
}


class TodoService(TodoServiceProtocol):
    """Validate A-owned Todo actions while B supplies runtime events."""

    @inject
    def __init__(
        self,
        repository: TodoRepositoryProtocol,
        space_access: SpaceAccessServiceProtocol,
        bot_repository: BotRepository,
        collaboration: TodoCollaborationPlugin,
        execution: TodoExecutionServiceProtocol,
        artifacts: TodoArtifactServiceProtocol,
    ) -> None:
        self._repository = repository
        self._space_access = space_access
        self._bot_repository = bot_repository
        self._collaboration = collaboration
        self._execution = execution
        self._artifacts = artifacts

    def list_todos(
        self,
        *,
        space_id: int,
        actor_id: str,
        keyword: str | None,
        status: TodoStatus | str | None,
        page: int,
        page_size: int,
        bot_id: str | None = None,
        env: str | None = None,
    ) -> TodoPage:
        normalized_space_id = self._space_id(space_id)
        normalized_actor_id = self._required_text(
            actor_id, "actor_id", MAX_ACTOR_ID_LENGTH
        )
        normalized_status = self._status(status)
        self._pagination(page, page_size)
        self._space_access.require_space_member(
            space_id=normalized_space_id,
            user_id=normalized_actor_id,
            env=env,
        )
        return self._repository.list_todos(
            space_id=normalized_space_id,
            keyword=self._search_text(keyword),
            status=normalized_status,
            bot_id=self._optional_text(bot_id, "bot_id", MAX_BOT_ID_LENGTH),
            page=page,
            page_size=page_size,
        )

    def get_todo(
        self, *, todo_id: str, actor_id: str, env: str | None = None
    ) -> TodoRecord:
        todo, _normalized_actor_id = self._load_todo(
            todo_id=todo_id, actor_id=actor_id, env=env
        )
        return todo

    def create_todo(
        self,
        *,
        space_id: int,
        actor_id: str,
        title: str,
        description: str,
        bot_id: str,
        env: str | None = None,
    ) -> TodoRecord:
        normalized_space_id = self._space_id(space_id)
        normalized_actor_id = self._required_text(
            actor_id, "actor_id", MAX_ACTOR_ID_LENGTH
        )
        normalized_title = self._required_text(title, "title", MAX_TITLE_LENGTH)
        normalized_description = self._text_allow_empty(
            description, "description", MAX_DESCRIPTION_LENGTH
        )
        normalized_bot_id = self._required_text(bot_id, "bot_id", MAX_BOT_ID_LENGTH)
        space, _member = self._space_access.require_space_member(
            space_id=normalized_space_id,
            user_id=normalized_actor_id,
            env=env,
        )
        bot = resolve_todo_bot(
            bot_repository=self._bot_repository,
            space=space,
            actor_id=normalized_actor_id,
            bot_id=normalized_bot_id,
        )
        require_todo_bot_owner_id(bot)
        return self._repository.create_todo(
            space_id=normalized_space_id,
            title=normalized_title,
            description=normalized_description,
            bot_id=normalized_bot_id,
            bot_name=todo_bot_name(bot, normalized_bot_id),
            created_by=normalized_actor_id,
        )

    async def start_todo(
        self, *, todo_id: str, actor_id: str, env: str | None = None
    ) -> TodoRecord:
        todo, _normalized_actor_id = self._load_todo(
            todo_id=todo_id, actor_id=actor_id, env=env
        )
        if todo.status is not TodoStatus.PENDING:
            raise TodoStateConflictError("Only pending Todos can run")
        return await self._execution.dispatch_pending(
            todo_id=todo.todo_id,
            env=env,
        )

    def list_events(
        self, *, todo_id: str, actor_id: str, env: str | None = None
    ) -> tuple[TodoEventRecord, ...]:
        todo, normalized_actor_id = self._load_todo(
            todo_id=todo_id, actor_id=actor_id, env=env
        )
        if not todo.collab_context_id:
            return ()
        context = self._collaboration.get_context(
            collab_context_id=todo.collab_context_id,
            actor_id=normalized_actor_id,
        )
        return self._to_event_records(context.events)

    def list_interactions(
        self, *, todo_id: str, actor_id: str, env: str | None = None
    ) -> tuple[TodoEventRecord, ...]:
        todo, normalized_actor_id = self._load_todo(
            todo_id=todo_id, actor_id=actor_id, env=env
        )
        if not todo.collab_context_id:
            return ()
        context = self._collaboration.get_context(
            collab_context_id=todo.collab_context_id,
            actor_id=normalized_actor_id,
        )
        events = tuple(
            event
            for event in self._to_event_records(context.events)
            if event.event_type in _INTERACTION_EVENT_TYPES
        )
        if not any(event.event_type == _TASK_COMPLETED_EVENT for event in events):
            return events
        artifacts = self._artifacts.list_artifacts(
            todo_id=todo.todo_id,
            actor_id=normalized_actor_id,
            env=env,
        )
        return self._with_bot_text_artifacts(events, artifacts)

    async def create_interaction(
        self,
        *,
        todo_id: str,
        actor_id: str,
        author_name: str | None,
        message: str,
        env: str | None = None,
    ) -> TodoEventRecord:
        todo, normalized_actor_id = self._load_todo(
            todo_id=todo_id, actor_id=actor_id, env=env
        )
        normalized_author_name = self._optional_text(
            author_name, "author_name", MAX_ACTOR_NAME_LENGTH
        )
        normalized_message = self._required_text(message, "message", MAX_MESSAGE_LENGTH)
        if todo.status is not TodoStatus.PENDING_ACCEPTANCE:
            raise TodoStateConflictError("Interactions require pending acceptance")
        context_id = self._require_context(todo)
        context = self._collaboration.get_context(
            collab_context_id=context_id,
            actor_id=normalized_actor_id,
        )
        # Claim the interaction synchronously before delivering it to B. This
        # closes the product-state window where a persisted Human event can
        # still be followed by another create-interaction request.
        self._repository.transition_status(
            todo_id=todo.todo_id,
            expected_status=TodoStatus.PENDING_ACCEPTANCE,
            new_status=TodoStatus.IN_PROGRESS,
        )
        payload: dict[str, Any] = {"content": normalized_message}
        if normalized_author_name is not None:
            payload["author_name"] = normalized_author_name
        event = self._collaboration.append_event(
            collab_context_id=context_id,
            request=TodoCollaborationEventRequest(
                expected_version=context.context_version,
                event_kind=_HUMAN_INTERACTION_EVENT,
                primitive_type="Message",
                actor_id=normalized_actor_id,
                actor_type="human",
                parent_event=context.current_event_id,
                visibility="workspace",
                payload=payload,
            ),
        )
        # A claimed interaction is durable work for the scheduler. Returning
        # before executor delivery is intentional and is required by contract.
        return self._to_event_record(event)

    def submit_acceptance(
        self,
        *,
        todo_id: str,
        actor_id: str,
        verdict: AcceptanceVerdict | str,
        decision_id: str,
        message: str | None,
        targets: tuple[TodoAcceptanceTarget, ...] = (),
        env: str | None = None,
    ) -> TodoRecord:
        todo, normalized_actor_id = self._load_todo(
            todo_id=todo_id, actor_id=actor_id, env=env
        )
        normalized_verdict = self._verdict(verdict)
        normalized_decision_id = self._required_text(
            decision_id,
            "decision_id",
            MAX_ACCEPTANCE_ID_LENGTH,
        )
        normalized_message = self._acceptance_message(
            verdict=normalized_verdict,
            message=message,
        )
        normalized_targets = self._acceptance_targets(
            verdict=normalized_verdict,
            targets=targets,
        )
        context_id = self._require_context(todo)
        context = self._collaboration.get_context(
            collab_context_id=context_id,
            actor_id=normalized_actor_id,
        )
        decision_events = context.events
        decision_event = self._find_decision_event(
            decision_events,
            decision_id=normalized_decision_id,
        )
        if decision_event is None:
            if todo.status is not TodoStatus.PENDING_ACCEPTANCE:
                raise TodoStateConflictError("Todo is not pending acceptance")
            if self._find_current_acceptance_decision(decision_events) is not None:
                raise TodoStateConflictError(
                    "acceptance round already has a different decision"
                )
            event_type = (
                _TASK_ACCEPTED_EVENT
                if normalized_verdict is AcceptanceVerdict.APPROVED
                else _TASK_REJECTED_EVENT
            )
            payload: dict[str, Any] = {
                "decision_id": normalized_decision_id,
                "todo_id": todo.todo_id,
                "verdict": normalized_verdict.value,
                "content": normalized_message,
                "author_id": normalized_actor_id,
                "targets": self._target_payloads(normalized_targets),
            }
            try:
                decision_event = self._collaboration.append_event(
                    collab_context_id=context_id,
                    request=TodoCollaborationEventRequest(
                        expected_version=context.context_version,
                        event_kind=event_type,
                        primitive_type="Task",
                        actor_id=normalized_actor_id,
                        actor_type="human",
                        parent_event=context.current_event_id,
                        visibility="workspace",
                        payload=payload,
                    ),
                )
                decision_events = (*decision_events, decision_event)
            except TodoCollaborationError:
                context = self._collaboration.get_context(
                    collab_context_id=context_id,
                    actor_id=normalized_actor_id,
                )
                decision_events = context.events
                decision_event = self._find_decision_event(
                    decision_events,
                    decision_id=normalized_decision_id,
                )
                if decision_event is None:
                    if (
                        self._find_current_acceptance_decision(decision_events)
                        is not None
                    ):
                        raise TodoStateConflictError(
                            "acceptance round already has a different decision"
                        )
                    raise

        self._validate_decision_event(
            decision_event,
            todo_id=todo.todo_id,
            actor_id=normalized_actor_id,
            verdict=normalized_verdict,
            message=normalized_message,
            targets=normalized_targets,
        )
        if (
            todo.status is TodoStatus.PENDING_ACCEPTANCE
            and not self._is_current_acceptance_decision(
                decision_events,
                decision_event=decision_event,
            )
        ):
            raise TodoStateConflictError(
                "decision_id belongs to an earlier acceptance round"
            )
        return self._repository.record_acceptance(
            todo_id=todo.todo_id,
            acceptance_id=normalized_decision_id,
            verdict=normalized_verdict,
            message=normalized_message,
            author_id=normalized_actor_id,
        )

    def _load_todo(
        self, *, todo_id: str, actor_id: str, env: str | None
    ) -> tuple[TodoRecord, str]:
        normalized_todo_id = self._required_text(todo_id, "todo_id", MAX_TODO_ID_LENGTH)
        normalized_actor_id = self._required_text(
            actor_id, "actor_id", MAX_ACTOR_ID_LENGTH
        )
        todo = self._repository.get_todo(todo_id=normalized_todo_id)
        self._require_todo_space_member(todo, normalized_actor_id, env)
        return todo, normalized_actor_id

    @classmethod
    def _with_bot_text_artifacts(
        cls,
        events: tuple[TodoEventRecord, ...],
        artifacts: tuple[TodoPersistedArtifactRecord, ...],
    ) -> tuple[TodoEventRecord, ...]:
        """Join Bot text outputs to the completion of the run that produced them.

        Text is persisted as an A-side artifact, while the compact event stream
        intentionally exposes only its artifact metadata. The interaction view,
        in contrast, is a chat projection and must inline the text.
        """

        text_artifacts = tuple(
            artifact
            for artifact in artifacts
            if artifact.artifact_kind is TodoArtifactKind.TEXT
        )
        if not text_artifacts:
            return events

        interactions: list[TodoEventRecord] = []
        previous_completion: float | None = None
        for event in events:
            if event.event_type != _TASK_COMPLETED_EVENT:
                interactions.append(event)
                continue

            completed_at = _event_timestamp(event.created_at)
            bot_text = _format_text_artifacts(
                tuple(
                    artifact
                    for artifact in text_artifacts
                    if _produced_for_completion(
                        artifact=artifact,
                        previous_completion=previous_completion,
                        completed_at=completed_at,
                    )
                )
            )
            content = event.content
            if bot_text:
                content = f"{event.content}\n\n{bot_text}".strip()
            if completed_at is not None:
                previous_completion = completed_at
            interactions.append(replace(event, content=content))
        return tuple(interactions)

    def _require_context(self, todo: TodoRecord) -> str:
        if not todo.collab_context_id:
            raise TodoNotFoundError("Todo collaboration Context is missing")
        return todo.collab_context_id

    def _to_event_records(
        self, events: Iterable[TodoCollaborationEvent]
    ) -> tuple[TodoEventRecord, ...]:
        return tuple(self._to_event_record(event) for event in events)

    @staticmethod
    def _to_event_record(event: TodoCollaborationEvent) -> TodoEventRecord:
        return TodoEventRecord(
            event_id=event.event_id,
            event_seq=event.event_seq,
            event_type=event.event_type,
            actor_id=event.actor_id,
            actor_type=event.actor_type,
            content=event.content,
            execution_id=event.execution_id,
            context_version=event.context_version,
            status=event.status,
            artifacts=tuple(
                TodoEventArtifactRecord(
                    artifact_id=artifact.artifact_id or None,
                    name=artifact.name,
                    url=artifact.url,
                )
                for artifact in event.artifacts
            ),
            created_at=event.created_at,
            payload=dict(event.payload),
        )

    @classmethod
    def _acceptance_message(
        cls,
        *,
        verdict: AcceptanceVerdict,
        message: str | None,
    ) -> str:
        if verdict is AcceptanceVerdict.REJECTED:
            return cls._required_text(message, "message", MAX_MESSAGE_LENGTH)
        if message is None:
            return ""
        return cls._text_allow_empty(message, "message", MAX_MESSAGE_LENGTH)

    @classmethod
    def _acceptance_targets(
        cls,
        *,
        verdict: AcceptanceVerdict,
        targets: tuple[TodoAcceptanceTarget, ...],
    ) -> tuple[TodoAcceptanceTarget, ...]:
        if not isinstance(targets, tuple):
            raise ValidationError("targets must be a tuple")
        if len(targets) > MAX_ACCEPTANCE_TARGETS:
            raise ValidationError("targets exceeds the maximum count")
        if verdict is AcceptanceVerdict.APPROVED and targets:
            raise ValidationError("approved acceptance must not contain targets")
        if verdict is AcceptanceVerdict.REJECTED and not targets:
            raise ValidationError("rejected acceptance requires at least one target")

        normalized: list[TodoAcceptanceTarget] = []
        seen: set[tuple[TodoAcceptanceTargetKind, str]] = set()
        for target in targets:
            if not isinstance(target, TodoAcceptanceTarget):
                raise ValidationError("targets contains an invalid target")
            try:
                kind = TodoAcceptanceTargetKind(target.kind)
            except ValueError as exc:
                raise ValidationError("target kind is invalid") from exc
            max_length = (
                MAX_EVENT_ID_LENGTH
                if kind is TodoAcceptanceTargetKind.EVENT
                else MAX_ARTIFACT_ID_LENGTH
            )
            target_id = cls._required_text(target.target_id, "target id", max_length)
            if (
                isinstance(target.floor, bool)
                or not isinstance(target.floor, int)
                or target.floor < 1
            ):
                raise ValidationError("target floor must be a positive integer")
            key = (kind, target_id)
            if key in seen:
                raise ValidationError("targets must be unique")
            seen.add(key)
            normalized.append(
                TodoAcceptanceTarget(
                    kind=kind,
                    target_id=target_id,
                    floor=target.floor,
                )
            )
        return tuple(normalized)

    @staticmethod
    def _target_payloads(
        targets: tuple[TodoAcceptanceTarget, ...],
    ) -> list[dict[str, object]]:
        return [
            {
                "kind": target.kind.value,
                "id": target.target_id,
                "floor": target.floor,
            }
            for target in targets
        ]

    @staticmethod
    def _find_decision_event(
        events: Iterable[TodoCollaborationEvent],
        *,
        decision_id: str,
    ) -> TodoCollaborationEvent | None:
        for event in reversed(tuple(events)):
            if event.payload.get("decision_id") == decision_id:
                return event
        return None

    @staticmethod
    def _find_current_acceptance_decision(
        events: Iterable[TodoCollaborationEvent],
    ) -> TodoCollaborationEvent | None:
        for event in reversed(tuple(events)):
            if event.event_type == _TASK_COMPLETED_EVENT:
                return None
            if event.event_type in {_TASK_ACCEPTED_EVENT, _TASK_REJECTED_EVENT}:
                return event
        return None

    @classmethod
    def _is_current_acceptance_decision(
        cls,
        events: Iterable[TodoCollaborationEvent],
        *,
        decision_event: TodoCollaborationEvent,
    ) -> bool:
        current = cls._find_current_acceptance_decision(events)
        return current is not None and current.event_id == decision_event.event_id

    @classmethod
    def _validate_decision_event(
        cls,
        event: TodoCollaborationEvent,
        *,
        todo_id: str,
        actor_id: str,
        verdict: AcceptanceVerdict,
        message: str,
        targets: tuple[TodoAcceptanceTarget, ...],
    ) -> None:
        expected_type = (
            _TASK_ACCEPTED_EVENT
            if verdict is AcceptanceVerdict.APPROVED
            else _TASK_REJECTED_EVENT
        )
        payload = event.payload
        if (
            event.event_type != expected_type
            or event.actor_id != actor_id
            or event.actor_type != "human"
            or payload.get("todo_id") != todo_id
            or payload.get("verdict") != verdict.value
            or payload.get("content") != message
            or payload.get("author_id") != actor_id
            or payload.get("targets") != cls._target_payloads(targets)
        ):
            raise TodoStateConflictError(
                "decision_id already belongs to a different acceptance decision"
            )

    def _require_todo_space_member(
        self, todo: TodoRecord, actor_id: str, env: str | None
    ) -> SpaceRecord:
        space, _member = self._space_access.require_space_member(
            space_id=todo.space_id,
            user_id=actor_id,
            env=env,
        )
        return space

    @staticmethod
    def _space_id(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValidationError("space_id must be a positive integer")
        return value

    @staticmethod
    def _pagination(page: int, page_size: int) -> None:
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ValidationError("page must be at least 1")
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or page_size < 1
            or page_size > 100
        ):
            raise ValidationError("page_size must be between 1 and 100")

    @classmethod
    def _status(cls, value: TodoStatus | str | None) -> TodoStatus | None:
        if value is None:
            return None
        if isinstance(value, TodoStatus):
            return value
        if not isinstance(value, str):
            raise ValidationError("status must be a string")
        try:
            return TodoStatus(value.strip())
        except ValueError as exc:
            raise ValidationError("status is invalid") from exc

    @classmethod
    def _verdict(cls, value: AcceptanceVerdict | str) -> AcceptanceVerdict:
        if isinstance(value, AcceptanceVerdict):
            return value
        if not isinstance(value, str):
            raise ValidationError("verdict must be a string")
        try:
            return AcceptanceVerdict(value.strip())
        except ValueError as exc:
            raise ValidationError("verdict is invalid") from exc

    @staticmethod
    def _required_text(value: object, field: str, max_length: int) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"{field} must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValidationError(f"{field} must not be empty")
        if len(normalized) > max_length:
            raise ValidationError(f"{field} exceeds the maximum length")
        return normalized

    @classmethod
    def _optional_text(cls, value: object, field: str, max_length: int) -> str | None:
        if value is None:
            return None
        return cls._required_text(value, field, max_length)

    @staticmethod
    def _text_allow_empty(value: object, field: str, max_length: int) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"{field} must be a string")
        normalized = value.strip()
        if len(normalized) > max_length:
            raise ValidationError(f"{field} exceeds the maximum length")
        return normalized

    @staticmethod
    def _search_text(value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValidationError("keyword must be a string")
        keyword = value.strip()
        return keyword or None


def _format_text_artifacts(
    artifacts: tuple[TodoPersistedArtifactRecord, ...],
) -> str:
    texts: list[str] = []
    for artifact in artifacts:
        text = artifact.content.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        if len(artifacts) == 1:
            texts.append(text)
        else:
            texts.append(f"{artifact.artifact_name}\n{text}")
    return "\n\n".join(texts)


def _event_timestamp(value: str) -> float | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _timestamp(parsed)


def _produced_for_completion(
    *,
    artifact: TodoPersistedArtifactRecord,
    previous_completion: float | None,
    completed_at: float | None,
) -> bool:
    created_at = _timestamp(artifact.created_at)
    if previous_completion is not None and created_at <= previous_completion:
        return False
    return completed_at is None or created_at <= completed_at


def _timestamp(value: datetime) -> float:
    normalized = (
        value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    )
    return normalized.timestamp()
