"""Server-managed list types cannot be changed through frontend corrections."""

RUNTIME_ARTIFACT_USER_LIST_TYPE = "service_bot_runtime_artifact"


class ServerManagedUserListError(PermissionError):
    """The public correction API cannot update this server-managed switch."""
