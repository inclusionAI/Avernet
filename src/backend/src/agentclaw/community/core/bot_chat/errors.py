class BotChatError(Exception):
    """Base exception for bot chat module."""


class SessionNotFoundError(BotChatError):
    """Raised when a conversation session is not found or not accessible."""


class LangfuseAPIError(BotChatError):
    """Raised when Langfuse API call fails."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
