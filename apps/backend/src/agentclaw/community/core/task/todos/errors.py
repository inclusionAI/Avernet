"""Semantic failures for the Todo workbench domain."""

from agentclaw.community.core.errors import (
    Conflict,
    Forbidden,
    InternalError,
    NotFound,
    Unauthorized,
    ValidationError,
)


class TodoNotFoundError(NotFound):
    """The requested Todo does not exist in the current tenant/environment."""


class TodoStateConflictError(Conflict):
    """The requested mutation is not legal in the Todo's current state."""


class TodoBotScopeError(Forbidden):
    """The selected Bot is not eligible for the Todo's Space."""


class TodoBotAmbiguousError(Conflict):
    """More than one eligible live Bot shares the selected Bot id."""


class TodoExecutionAuthError(Unauthorized):
    """The Bot execution credential is missing, stale, or mismatched."""


class TodoArtifactNotFoundError(NotFound):
    """The requested Todo artifact does not exist under the addressed Todo."""


class TodoArtifactContentError(ValidationError):
    """A reported artifact does not satisfy its kind-specific contract."""


class TodoArtifactUploadTooLargeError(ValidationError):
    """An uploaded file exceeds the shared artifact storage limit."""


class TodoArtifactObjectNotFoundError(NotFound):
    """The artifact row exists but its object bytes are missing."""


class TodoArtifactStorageNotConfiguredError(InternalError):
    """No deployment object-storage plugin is available for Todo files."""


class TodoArtifactStorageError(InternalError):
    """The configured object store failed an operation."""


class TodoSourceAuthError(Unauthorized):
    """The Human request cannot obtain a usable source credential."""


class TodoSourceUnavailableError(InternalError):
    """The configured source service or MCP endpoint is unavailable."""


class TodoSourceResponseError(InternalError):
    """The source returned a response that cannot be normalized safely."""


class TodoSourceWorkItemNotFoundError(NotFound):
    """The selected source work item is absent or no longer visible."""


class TodoSourceAlreadyImportedError(Conflict):
    """The external work item already belongs to a Todo in this Space."""

    def __init__(self, existing_todo_id: str) -> None:
        super().__init__("Dima work item already imported")
        self.existing_todo_id = existing_todo_id


class TodoTemplateValidationError(ValidationError):
    """A Todo template definition violates the supported product contract."""


class TodoTemplateNotFoundError(NotFound):
    """The requested Todo template or version is outside the current scope."""


class TodoTemplateStateConflictError(Conflict):
    """The requested Todo template mutation is illegal in its current state."""
