"""Domain errors for Bot inventory and local Bot public workflows."""
from __future__ import annotations


class BotInventoryError(Exception):
    """Base error for bot inventory failures."""


class BotInventoryOperationNotAllowedError(BotInventoryError):
    """The requested inventory/local operation is not allowed."""


class BotInventoryPermissionError(BotInventoryError):
    """The requested Bot is not visible to the caller in this context."""


class BotInventoryUpstreamError(BotInventoryError):
    """An upstream dependency needed by the inventory/local workflow failed."""
