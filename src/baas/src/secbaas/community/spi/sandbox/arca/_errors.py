"""Project-owned Arca sandbox error types.

These replace direct use of arca.model.exceptions in the core layer.
The _arca_sdk.py plugin catches SDK exceptions and re-raises these.
"""


class ArcaSandboxError(RuntimeError):
    """Base exception for Arca sandbox operations."""


class ArcaSandboxNotFoundError(ArcaSandboxError):
    """Raised when a sandbox is not found."""


class ArcaSandboxTimeoutError(ArcaSandboxError):
    """Raised when a sandbox operation times out."""


class ArcaSandboxConnectionError(ArcaSandboxError):
    """Raised when connection to an Arca sandbox fails after retry.

    Attributes:
        sandbox_id: The sandbox ID that failed to connect.
        attempts: Number of connection attempts made.
    """

    def __init__(
        self,
        message: str,
        *,
        sandbox_id: str = "",
        attempts: int = 0,
    ) -> None:
        super().__init__(message)
        self.sandbox_id = sandbox_id
        self.attempts = attempts
