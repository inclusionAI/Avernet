"""Product policy and authorization orchestration for Todo workbench actions."""

from __future__ import annotations

from typing import Any, Iterable

from injector import inject

from agentclaw.community.core.errors import ValidationError
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.task.todos import (
    TodoRepositoryProtocol,
)
from agentclaw.community.core.spaces.models import SpaceRecord
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
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
    MAX_BOT_ID_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_MESSAGE_LENGTH,
    MAX_TITLE_LENGTH,
    MAX_TODO_ID_LENGTH,
    AcceptanceVerdict,
    TodoArtifactRecord,
    TodoEventRecord,
    TodoPage,
    TodoRecord,
    TodoStatus,
)
from agentclaw.community.core.task.todos.service_protocol import TodoServiceProtocol
from agentclaw.community.plugin_api.todo_collaboration import (
    TodoCollaborationEvent,
    TodoCollaborationEventRequest,
    TodoCollaborationPlugin,
)

_HUMAN_INTERACTION_EVENT = "user_interaction.posted"


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
    ) -> None:
        self._repository = repository
        self._space_access = space_access
        self._bot_repository = bot_repository
        self._collaboration = collaboration
        self._execution = execution

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
        return tuple(
            event
            for event in self.list_events(todo_id=todo_id, actor_id=actor_id, env=env)
            if event.event_type == _HUMAN_INTERACTION_EVENT
        )

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
        await self._execution.resume_interaction(
            todo_id=todo.todo_id,
            actor_id=normalized_actor_id,
            triggered_by_event_id=event.event_id,
        )
        return self._to_event_record(event)

    def submit_acceptance(
        self,
        *,
        todo_id: str,
        actor_id: str,
        verdict: AcceptanceVerdict | str,
        message: str | None,
        env: str | None = None,
    ) -> TodoRecord:
        todo, normalized_actor_id = self._load_todo(
            todo_id=todo_id, actor_id=actor_id, env=env
        )
        normalized_verdict = self._verdict(verdict)
        normalized_message = (
            ""
            if message is None
            else self._text_allow_empty(message, "message", MAX_MESSAGE_LENGTH)
        )
        if todo.status is not TodoStatus.PENDING_ACCEPTANCE:
            raise TodoStateConflictError("Todo is not pending acceptance")
        return self._repository.record_acceptance(
            todo_id=todo.todo_id,
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
                TodoArtifactRecord(
                    artifact_id=artifact.artifact_id or None,
                    name=artifact.name,
                    url=artifact.url,
                )
                for artifact in event.artifacts
            ),
            created_at=event.created_at,
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
