"""Stable public error mapping for the Todo workbench."""

from agentclaw.community.core.task.todos.errors import (
    TodoArtifactContentError,
    TodoArtifactNotFoundError,
    TodoArtifactObjectNotFoundError,
    TodoArtifactStorageError,
    TodoArtifactStorageNotConfiguredError,
    TodoArtifactUploadTooLargeError,
    TodoBotAmbiguousError,
    TodoBotScopeError,
    TodoExecutionAuthError,
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
    TodoExecutionAuthError: (401, "Invalid Todo execution credential"),
    TodoArtifactNotFoundError: (404, "Not found"),
    TodoArtifactContentError: (422, "Invalid Todo artifact"),
    TodoArtifactUploadTooLargeError: (413, "Todo artifact is too large"),
    TodoArtifactObjectNotFoundError: (404, "Not found"),
    TodoArtifactStorageNotConfiguredError: (503, "Todo artifact storage unavailable"),
    TodoArtifactStorageError: (502, "Todo artifact storage operation failed"),
    TodoCollaborationError: (503, "Todo collaboration service unavailable"),
}

__all__ = ["TODO_HTTP_ERRORS"]
