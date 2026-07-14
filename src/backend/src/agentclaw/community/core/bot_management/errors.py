"""Domain errors owned by the bot-management context."""


class DefaultBotPassportRepairError(Exception):
    """Stable repair failure that the delivery adapter can serialize."""

    def __init__(self, message: str, *, error_code: int) -> None:
        super().__init__(message)
        self.error_code = error_code
