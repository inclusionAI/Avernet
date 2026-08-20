"""Domain errors for Channel management."""


class ChannelError(Exception):
    """Base error for Channel management."""


class ChannelNotFoundError(ChannelError):
    """The addressed Channel does not exist in the addressed Bot scope."""


class ChannelEditLockedError(ChannelError):
    """A collaborator must hold the addressed Bot edit lock before writing."""


class ChannelSyncError(ChannelError):
    """The Channel was valid but could not be synchronized to its runtime."""
