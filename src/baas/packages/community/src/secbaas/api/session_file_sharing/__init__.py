"""Session File Sharing — public API contracts.

Re-exports error classes and request/response models for the Session File
Sharing HTTP API.  Session models are independent of Bot Device File Transfer
models (per D-05).  Bot-originated error codes with shared semantics are
re-exported without a SESSION_ prefix (per D-04).
"""

from ._errors import (
    SessionFileSharingError,
    SourceTransferNotFoundError,
    SourceTransferNotReadyError,
    StagingObjectNotFoundError,
    TransferNotFoundError,
    TransferNotTerminalError,
    TransferStateConflictError,
)
from ._models import (
    SessionCancelUploadResponse,
    SessionCompleteUploadResponse,
    SessionDeleteTransferResponse,
    SessionGetTransferStatusResponse,
    SessionGetUploadUrlRequest,
    SessionGetUploadUrlResponse,
    SessionShareLinkRequest,
    SessionShareLinkResponse,
)
from ._protocols import SessionFileSharingDispatcher

__all__ = [
    # Protocols
    "SessionFileSharingDispatcher",
    # Errors
    "StagingObjectNotFoundError",
    "SessionFileSharingError",
    "SourceTransferNotFoundError",
    "SourceTransferNotReadyError",
    "TransferNotFoundError",
    "TransferNotTerminalError",
    "TransferStateConflictError",
    # Models
    "SessionCancelUploadResponse",
    "SessionCompleteUploadResponse",
    "SessionDeleteTransferResponse",
    "SessionGetTransferStatusResponse",
    "SessionGetUploadUrlRequest",
    "SessionGetUploadUrlResponse",
    "SessionShareLinkRequest",
    "SessionShareLinkResponse",
]
