"""Product policy and authorization orchestration for Todo workbench actions."""

from __future__ import annotations

from typing import Any, Iterable

from injector import inject

from agentclaw.community.core.errors import ValidationError
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.task.todos import (
    TodoRepositoryProtocol,
)
from agentclaw.community.core.spaces.models import SpaceRecord, SpaceType
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
from agentclaw.community.core.spaces.space_service_protocol import (
    SpaceMemberServiceProtocol,
)
from agentclaw.community.core.task.todos.errors import (
    TodoBotAmbiguousError,
    TodoBotScopeError,
    TodoNotFoundError,
    TodoStateConflictError,
)
from agentclaw.community.core.task.todos.models import (
    MAX_ACTOR_ID_LENGTH,
    MAX_ACTOR_NAME_LENGTH,
    MAX_BOT_ID_LENGTH,
    MAX_COLLAB_CONTEXT_ID_LENGTH,
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
    TodoCollaborationContextRequest,
    TodoCollaborationEvent,
    TodoCollaborationEventRequest,
    TodoCollaborationPlugin,
)

_HUMAN_INTERACTION_EVENT = "human_interaction"
_HUMAN_ACCEPTANCE_EVENT = "human_acceptance"
_FINISH_STATUSES = frozenset(
    {"pending_acceptance", "awaiting_acceptance", "finished", "succeeded", "completed"}
)


class TodoService(TodoServiceProtocol):
    """Validate input, enforce Space/Bot eligibility, and project B events."""

    @inject
    def __init__(
        self,
        repository: TodoRepositoryProtocol,
        space_access: SpaceAccessServiceProtocol,
        space_members: SpaceMemberServiceProtocol,
        bot_repository: BotRepository,
        collaboration: TodoCollaborationPlugin,
    ) -> None:
        self._repository = repository
        self._space_access = space_access
        self._space_members = space_members
        self._bot_repository = bot_repository
        self._collaboration = collaboration

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
        normalized_keyword = self._search_text(keyword)
        normalized_status = self._status(status)
        normalized_bot_id = self._optional_text(
            bot_id, "bot_id", MAX_BOT_ID_LENGTH
        )
        self._pagination(page, page_size)
        self._space_access.require_space_member(
            space_id=normalized_space_id,
            user_id=normalized_actor_id,
            env=env,
        )
        return self._repository.list_todos(
            space_id=normalized_space_id,
            keyword=normalized_keyword,
            status=normalized_status,
            bot_id=normalized_bot_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    def get_todo(
        self, *, todo_id: str, actor_id: str, env: str | None = None
    ) -> TodoRecord:
        normalized_todo_id = self._required_text(todo_id, "todo_id", MAX_TODO_ID_LENGTH)
        normalized_actor_id = self._required_text(
            actor_id, "actor_id", MAX_ACTOR_ID_LENGTH
        )
        todo = self._repository.get_todo(todo_id=normalized_todo_id)
        self._require_todo_space_member(todo, normalized_actor_id, env)
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
        bot = self._resolve_bot(
            space=space,
            actor_id=normalized_actor_id,
            bot_id=normalized_bot_id,
        )
        return self._repository.create_todo(
            space_id=normalized_space_id,
            title=normalized_title,
            description=normalized_description,
            bot_id=normalized_bot_id,
            bot_name=self._bot_name(bot, normalized_bot_id),
            created_by=normalized_actor_id,
        )

    def update_todo(
        self,
        *,
        todo_id: str,
        actor_id: str,
        title: str | None,
        description: str | None,
        bot_id: str | None,
        env: str | None = None,
    ) -> TodoRecord:
        normalized_todo_id = self._required_text(todo_id, "todo_id", MAX_TODO_ID_LENGTH)
        normalized_actor_id = self._required_text(
            actor_id, "actor_id", MAX_ACTOR_ID_LENGTH
        )
        if title is None and description is None and bot_id is None:
            raise ValidationError("at least one Todo field must be provided")

        todo = self._repository.get_todo(todo_id=normalized_todo_id)
        space = self._require_todo_space_member(todo, normalized_actor_id, env)
        normalized_title = (
            todo.title
            if title is None
            else self._required_text(title, "title", MAX_TITLE_LENGTH)
        )
        normalized_description = (
            todo.description
            if description is None
            else self._text_allow_empty(
                description, "description", MAX_DESCRIPTION_LENGTH
            )
        )
        normalized_bot_id = (
            todo.bot_id
            if bot_id is None
            else self._required_text(bot_id, "bot_id", MAX_BOT_ID_LENGTH)
        )
        bot_name = todo.bot_name
        if bot_id is not None:
            bot = self._resolve_bot(
                space=space,
                actor_id=normalized_actor_id,
                bot_id=normalized_bot_id,
            )
            bot_name = self._bot_name(bot, normalized_bot_id)

        return self._repository.update_draft(
            todo_id=normalized_todo_id,
            title=normalized_title,
            description=normalized_description,
            bot_id=normalized_bot_id,
            bot_name=bot_name,
        )

    def start_todo(
        self, *, todo_id: str, actor_id: str, env: str | None = None
    ) -> TodoRecord:
        todo = self.get_todo(todo_id=todo_id, actor_id=actor_id, env=env)
        normalized_actor_id = self._required_text(
            actor_id, "actor_id", MAX_ACTOR_ID_LENGTH
        )
        if todo.status is not TodoStatus.DRAFT:
            raise TodoStateConflictError("Only draft Todos can start")
        collab_context_id = todo.collab_context_id
        if not collab_context_id:
            space, _member = self._space_access.require_space_member(
                space_id=todo.space_id,
                user_id=normalized_actor_id,
                env=env,
            )
            context = self._collaboration.create_context(
                TodoCollaborationContextRequest(
                    task_id=todo.todo_id,
                    task_title=todo.title,
                    collaboration_goal=todo.description or todo.title,
                    collaborators=self._collaborators(
                        space_id=space.id,
                        actor_id=normalized_actor_id,
                    ),
                    owner_id=normalized_actor_id,
                    bot_id=todo.bot_id,
                )
            )
            collab_context_id = self._required_text(
                context.collab_context_id,
                "collab_context_id",
                MAX_COLLAB_CONTEXT_ID_LENGTH,
            )
        return self._repository.start_if_draft(
            todo_id=todo.todo_id,
            collab_context_id=collab_context_id,
        )

    def list_events(
        self, *, todo_id: str, actor_id: str, env: str | None = None
    ) -> tuple[TodoEventRecord, ...]:
        todo = self.get_todo(todo_id=todo_id, actor_id=actor_id, env=env)
        if not todo.collab_context_id:
            return ()
        events = self._collaboration.list_events(
            collab_context_id=todo.collab_context_id
        )
        if self._should_mark_pending_acceptance(events):
            self._repository.mark_pending_acceptance_if_in_progress(
                todo_id=todo.todo_id
            )
        return tuple(self._to_event_records(events))

    def list_interactions(
        self, *, todo_id: str, actor_id: str, env: str | None = None
    ) -> tuple[TodoEventRecord, ...]:
        events = self.list_events(todo_id=todo_id, actor_id=actor_id, env=env)
        return tuple(
            event
            for event in events
            if event.event_type in (_HUMAN_INTERACTION_EVENT, _HUMAN_ACCEPTANCE_EVENT)
        )

    def create_interaction(
        self,
        *,
        todo_id: str,
        actor_id: str,
        author_name: str | None,
        message: str,
        env: str | None = None,
    ) -> TodoEventRecord:
        todo = self.get_todo(todo_id=todo_id, actor_id=actor_id, env=env)
        normalized_actor_id = self._required_text(
            actor_id, "actor_id", MAX_ACTOR_ID_LENGTH
        )
        self._optional_text(author_name, "author_name", MAX_ACTOR_NAME_LENGTH)
        normalized_message = self._required_text(message, "message", MAX_MESSAGE_LENGTH)
        if todo.status is not TodoStatus.PENDING_ACCEPTANCE:
            raise TodoStateConflictError("Interactions require pending acceptance")

        prior_events = ()
        if todo.collab_context_id:
            prior_events = self._collaboration.list_events(
                collab_context_id=todo.collab_context_id
            )
        event = self._collaboration.append_event(
            collab_context_id=self._require_context(todo),
            request=TodoCollaborationEventRequest(
                event_type=_HUMAN_INTERACTION_EVENT,
                actor_id=normalized_actor_id,
                content=normalized_message,
                boundary_event_seq=max(
                    (event.event_seq for event in prior_events), default=0
                ),
                payload={"author_name": author_name},
            ),
        )
        self._repository.mark_running_if_pending_acceptance(todo_id=todo.todo_id)
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
        todo = self.get_todo(todo_id=todo_id, actor_id=actor_id, env=env)
        normalized_actor_id = self._required_text(
            actor_id, "actor_id", MAX_ACTOR_ID_LENGTH
        )
        normalized_verdict = self._verdict(verdict)
        normalized_message = (
            ""
            if message is None
            else self._text_allow_empty(message, "message", MAX_MESSAGE_LENGTH)
        )
        if todo.status is not TodoStatus.PENDING_ACCEPTANCE:
            raise TodoStateConflictError("Todo is not pending acceptance")

        context_id = self._require_context(todo)
        prior_events = self._collaboration.list_events(collab_context_id=context_id)
        self._collaboration.append_event(
            collab_context_id=context_id,
            request=TodoCollaborationEventRequest(
                event_type=_HUMAN_ACCEPTANCE_EVENT,
                actor_id=normalized_actor_id,
                content=normalized_message,
                boundary_event_seq=max(
                    (event.event_seq for event in prior_events), default=0
                ),
                payload={"verdict": normalized_verdict.value},
            ),
        )
        return self._repository.accept_if_pending(
            todo_id=todo.todo_id,
            verdict=normalized_verdict,
            message=normalized_message,
            author_id=normalized_actor_id,
        )

    def _collaborators(self, *, space_id: int, actor_id: str) -> tuple[str, ...]:
        total, summaries = self._space_members.list_members(
            space_id=space_id,
            actor_id=actor_id,
            keyword=None,
            page_no=1,
            page_size=100,
        )
        normalized_members = (
            self._required_text(
                summary.member.user_id, "collaborator", MAX_ACTOR_ID_LENGTH
            )
            for summary in summaries
        )
        collaborators: dict[str, None] = {
            normalized_id: None for normalized_id in normalized_members
        }
        collaborators.setdefault(actor_id)
        # The Todo is single-Bot and Space member lists are bounded; a second page
        # would make this initial collaboration boundary dependent on UI pagination.
        if total > len(collaborators):
            raise ValidationError("Space has more than 100 members")
        return tuple(collaborators)

    def _require_context(self, todo: TodoRecord) -> str:
        if not todo.collab_context_id:
            raise TodoNotFoundError("Todo collaboration context is missing")
        return todo.collab_context_id

    def _should_mark_pending_acceptance(
        self, events: Iterable[TodoCollaborationEvent]
    ) -> bool:
        ordered = tuple(events)
        boundary_seq = max(
            (
                event.event_seq
                for event in ordered
                if event.event_type == _HUMAN_INTERACTION_EVENT
            ),
            default=0,
        )
        for event in ordered:
            if event.event_seq <= boundary_seq:
                continue
            if (
                event.event_type == "execution.status"
                and (event.status or "").lower() in _FINISH_STATUSES
            ):
                return True
            if event.event_type in {
                "execution.awaiting_acceptance",
                "execution.finished",
            }:
                return True
        return False

    def _to_event_records(
        self, events: Iterable[TodoCollaborationEvent]
    ) -> tuple[TodoEventRecord, ...]:
        return tuple(self._to_event_record(event) for event in events)

    @classmethod
    def _to_event_record(cls, event: TodoCollaborationEvent) -> TodoEventRecord:
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

    def _resolve_bot(
        self, *, space: SpaceRecord, actor_id: str, bot_id: str
    ) -> dict[str, Any]:
        candidates = self._bot_repository.list_live_by_bot_id(bot_id)
        eligible = [
            bot
            for bot in candidates
            if self._bot_in_scope(bot=bot, space=space, actor_id=actor_id)
        ]
        if not eligible:
            raise TodoBotScopeError("Bot is not available in this Space")
        if len(eligible) > 1:
            raise TodoBotAmbiguousError("Bot id is ambiguous in this Space")
        return eligible[0]

    @classmethod
    def _bot_in_scope(
        cls, *, bot: dict[str, Any], space: SpaceRecord, actor_id: str
    ) -> bool:
        bot_space_id = bot.get("space_id")
        if space.space_type == SpaceType.TEAM:
            return bot_space_id is not None and cls._same_space_id(
                bot_space_id, space.id
            )
        if space.space_type != SpaceType.PERSONAL:
            return False
        # Personal Spaces and Bots are both identified by the Human user id.
        # A matching space_id alone is not sufficient for legacy or corrupted
        # rows: the Space owner, current actor, and Bot owner must all agree.
        if not space.personal_owner_id or space.personal_owner_id != actor_id:
            return False
        bot_owner_id = bot.get("owner_id")
        if bot_owner_id != actor_id:
            return False
        if bot_space_id is not None:
            return cls._same_space_id(bot_space_id, space.id)
        return True

    @staticmethod
    def _same_space_id(value: object, space_id: int) -> bool:
        return str(value) == str(space_id)

    @classmethod
    def _bot_name(cls, bot: dict[str, Any], bot_id: str) -> str:
        value = bot.get("bot_name")
        if not isinstance(value, str) or not value.strip():
            return bot_id
        return value.strip()

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
        text = cls._required_text(value, field, max_length)
        return text

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
        if keyword:
            return keyword
        return None
