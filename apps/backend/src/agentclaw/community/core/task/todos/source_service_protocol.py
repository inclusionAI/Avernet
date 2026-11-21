"""Service API for importing external work items into editable Todo drafts."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.todos.source_models import (
    TodoSourceDraft,
    TodoSourceProject,
    TodoSourceWorkItem,
)
from agentclaw.community.plugin_api.auth import AuthRequestContext


@runtime_checkable
class TodoSourceServiceProtocol(Protocol):
    def list_projects(
        self,
        *,
        actor_id: str,
        auth_request: AuthRequestContext,
    ) -> tuple[TodoSourceProject, ...]: ...

    def list_work_items(
        self,
        *,
        actor_id: str,
        auth_request: AuthRequestContext,
        project_id: str,
        query: str | None = None,
    ) -> tuple[TodoSourceWorkItem, ...]: ...

    def load_work_item_draft(
        self,
        *,
        actor_id: str,
        auth_request: AuthRequestContext,
        project_id: str,
        work_item_id: str,
    ) -> TodoSourceDraft: ...
