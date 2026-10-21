"""Shared build errors; independent of orchestration and engine strategies."""

class BotBuildServiceError(Exception):
    """Bot build service error."""
    pass


class BotSourceNotFoundError(BotBuildServiceError):
    """Source bot not found error."""
    pass


class BotBuildMigrationError(BotBuildServiceError):
    """Bot migration error."""
    pass


