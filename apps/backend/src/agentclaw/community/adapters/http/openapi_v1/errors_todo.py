"""Stable public error mapping for the Todo workbench."""

from agentclaw.community.core.task.todos.errors import (
    TodoBotAmbiguousError,
    TodoBotScopeError,
    TodoNotFoundError,
    TodoStateConflictError,
)
from agentclaw.community.plugin_api.todo_collaboration import (
    TodoCollaborationError,
)

TODO_HTTP_ERRORS: dict[type[Exception], tuple[int, str]] = {
    TodoNotFoundError: (404, "Not found"),
    TodoStateConflictError: (409, "Todo state conflict"),
    TodoBotScopeError: (403, "Forbidden"),
    TodoBotAmbiguousError: (409, "Todo bot is ambiguous"),
    TodoCollaborationError: (503, "Todo collaboration service unavailable"),
}

__all__ = ["TODO_HTTP_ERRORS"]
