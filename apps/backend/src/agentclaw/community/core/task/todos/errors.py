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
