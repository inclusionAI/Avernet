"""Cron domain errors."""


class CronRelayError(RuntimeError):
    """Business error raised by cron relay before reaching adapter."""

    def __init__(self, message: str, error_code: int = 400):
        super().__init__(message)
        self.error_code = error_code
