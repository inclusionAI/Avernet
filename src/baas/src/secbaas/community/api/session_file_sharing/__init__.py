"""Session File Sharing — public API contracts.

Re-exports error classes, request/response models, and the Dispatcher
protocol for the Session File Sharing HTTP API.  Session models are
independent of Bot Device File Transfer models (per D-05).  Bot-originated
error codes with shared semantics are re-exported without a SESSION_ prefix
(per D-04).
"""

from ._errors import (
    StagingObjectNotFoundError,
    SessionFileSharingError,
    SourceTransferNotFoundError,
    SourceTransferNotReadyError,
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
    # Protocol
    "SessionFileSharingDispatcher",
]