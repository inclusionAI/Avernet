"""Persistence contract for A-owned Todo product records."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.todos.models import (
        AcceptanceVerdict,
        TodoPage,
        TodoRecord,
        TodoStatus,
    )
    from agentclaw.community.core.task.todos.source_models import TodoSourceInput
    from agentclaw.community.core.task.todos.stage_models import (
        TodoParticipantBinding,
        TodoStageRecord,
        TodoStageStatus,
    )
    from agentclaw.community.core.task.todos.stage_outcome_models import TodoStageOutcome


@runtime_checkable
class TodoRepositoryProtocol(Protocol):
    @abstractmethod
    def create_todo(
        self,
        *,
        space_id: int,
        title: str,
        description: str,
        bot_id: str | None,
        bot_name: str | None,
        created_by: str,
        source: TodoSourceInput | None = None,
    ) -> TodoRecord: ...

    @abstractmethod
    def create_todo_with_stages(
        self,
        *,
        record: TodoRecord,
        participant_bindings: tuple[TodoParticipantBinding, ...],
        stages: tuple[TodoStageRecord, ...],
    ) -> TodoRecord:
        """Atomically persist a templated Todo and its full stage graph."""
        ...

    @abstractmethod
    def get_todo(self, *, todo_id: str) -> TodoRecord: ...

    @abstractmethod
    def list_todos(
        self,
        *,
        space_id: int,
        keyword: str | None,
        status: TodoStatus | None,
        bot_id: str | None,
        page: int,
        page_size: int,
        order_by_status: bool = True,
    ) -> TodoPage:
        """Return a database-filtered page of persisted product Todos.

        An unfiltered page follows the public six-state display order when
        ``order_by_status`` is enabled. A status-filtered page remains
        oldest-first within that state.
        """

        ...

    @abstractmethod
    def list_todos_by_status(
        self,
        *,
        status: TodoStatus,
        limit: int,
    ) -> tuple[TodoRecord, ...]:
        """Return the oldest persisted Todos in one state for scheduler work."""
        ...

    @abstractmethod
    def bind_context_if_pending(
        self, *, todo_id: str, collab_context_id: str
    ) -> TodoRecord:
        """Attach a B Context only while the Todo remains pending."""
        ...

    @abstractmethod
    def transition_status(
        self,
        *,
        todo_id: str,
        expected_status: TodoStatus,
        new_status: TodoStatus,
    ) -> TodoRecord:
        """Compare and set the persisted product state under a row lock."""
        ...

    @abstractmethod
    def record_acceptance(
        self,
        *,
        todo_id: str,
        acceptance_id: str,
        verdict: AcceptanceVerdict,
        message: str,
        author_id: str,
    ) -> TodoRecord:
        """Atomically store the Human decision and resulting A state."""
        ...


    @abstractmethod
    def list_stages(self, *, todo_id: str) -> tuple[TodoStageRecord, ...]:
        """Return the fixed ordered stage timeline for one Todo."""
        ...

    @abstractmethod
    def apply_stage_outcome(
        self, *, outcome: TodoStageOutcome
    ) -> TodoRecord:
        """Atomically apply one stage outcome to the stage and Todo projection."""
        ...

    @abstractmethod
    def resume_todo(self, *, todo_id: str) -> TodoRecord:
        """Resume an interrupted or blocked Todo."""
        ...

    @abstractmethod
    def rerun_from_execution(
        self,
        *,
        todo_id: str,
        execution_id: str,
        stage_instance_id: str,
        invalidated_by_interaction_id: str,
    ) -> TodoRecord:
        """Invalidate the current target generation and all later stages."""
        ...

    @abstractmethod
    def cancel_todo(self, *, todo_id: str, reason: str | None = None) -> TodoRecord:
        """Cancel a Todo under a row lock."""
        ...

    @abstractmethod
    def bind_stage_context(
        self, *, todo_id: str, stage_instance_id: str, collab_context_id: str
    ) -> TodoRecord:
        """Bind the runtime Context to the current stage and Todo."""
        ...

    @abstractmethod
    def transition_stage_status(
        self,
        *,
        todo_id: str,
        stage_instance_id: str,
        expected_status: TodoStageStatus,
        new_status: TodoStageStatus,
        collab_context_id: str | None = None,
        state_reason: str | None = None,
        requested_action: str | None = None,
        pending_action_kind: str | None = None,
    ) -> TodoStageRecord:
        """Compare-and-set one Todo stage under a row lock."""
        ...
