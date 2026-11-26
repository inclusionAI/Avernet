"""HTTP mappings for managed Bot operation failures."""

from agentclaw.community.adapters.http.openapi_v1.errors import (
    BotOperationsForbiddenError,
    EngineOperationUnsupportedError,
)
from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError
from agentclaw.community.core.bot_management.snapshot_guard import (
    ManagedBotInputError,
    ManagedSnapshotConflictError,
    ManagedSnapshotPersistenceError,
)

MANAGED_BOT_HTTP_ERRORS = {
    BotOperationsForbiddenError: (403, "Forbidden"),
    EngineOperationUnsupportedError: (501, "Not supported for this engine"),
    BotAddressNotFoundError: (404, "Not found"),
    ManagedBotInputError: (400, "Invalid managed Bot request"),
    ManagedSnapshotConflictError: (409, "Template snapshot changed"),
    ManagedSnapshotPersistenceError: (500, "Internal error"),
}
