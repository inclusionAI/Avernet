"""Domain errors for Channel management."""


class ChannelError(Exception):
    """Base error for Channel management."""


class ChannelNotFoundError(ChannelError):
    """The addressed Channel does not exist in the addressed Bot scope."""


class ChannelEditLockedError(ChannelError):
    """A collaborator must hold the addressed Bot edit lock before writing."""


class ChannelSyncError(ChannelError):
    """The Channel was valid but could not be synchronized to its runtime."""


class ChannelModeViolationError(ChannelError):
    """The request's fields or binding mode conflict with the Channel's stored mode."""


class ChannelBindingConflictError(ChannelError):
    """BCS rejected the binding because it conflicts with an existing binding."""
