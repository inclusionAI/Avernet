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
