"""Cron domain errors."""


class CronRelayError(RuntimeError):
    """Business error surfaced by the cron relay."""

    def __init__(self, message: str, error_code: int = 400):
        super().__init__(message)
        self.error_code = error_code


class CronApiTimeoutError(CronRelayError):
    """Cron adapter read exceeded the configured request deadline."""

    def __init__(self, path: str, timeout: float) -> None:
        super().__init__(
            f"cron_api_timeout: no response within {timeout:g}s for {path}",
            error_code=504,
        )
