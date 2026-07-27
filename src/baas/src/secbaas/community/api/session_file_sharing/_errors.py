"""Session File Sharing domain exception definitions.

Defines Session-scoped error classes for the file sharing API.  These inherit
from DomainError (per D-01) rather than BotServiceError (per D-02) because
Session File Sharing is a separate domain from Bot Device File Transfer.

Four Bot-level error codes are re-exported from ``secbaas.community.api.bot_runtime``
without a SESSION_ prefix (per D-04).  They apply verbatim because the same
semantics (not-found, state-conflict, etc.) hold for Session transfers.
"""

from __future__ import annotations

from secbaas.community.api import DomainError


class SessionFileSharingError(DomainError):
    """Base exception for Session File Sharing operations.

    All Session file sharing errors inherit from this class rather than
    BotServiceError, keeping the two domains independent (per D-02).
    """

    error_code = "SESSION_FILE_SHARING_ERROR"
    http_status = 400


class SourceTransferNotFoundError(SessionFileSharingError):
    """The source transfer referenced in a share-link request does not exist.

    Carries ``transfer_id`` so callers can log or retry with a corrected ID.
    """

    error_code = "SOURCE_TRANSFER_NOT_FOUND"
    http_status = 404

    def __init__(self, transfer_id: str = ""):
        self.transfer_id = transfer_id
        super().__init__(f"Source upload transfer not found: {transfer_id}")


class SourceTransferNotReadyError(SessionFileSharingError):
    """The source transfer exists but is not yet in a shareable state (DONE).

    Carries ``transfer_id`` and ``current_status`` so callers can decide whether
    to poll and at what interval (per D-03).  These fields are surfaced in
    ``HTTPException.detail`` for structured 409 responses.
    """

    error_code = "SOURCE_TRANSFER_NOT_READY"
    http_status = 409

    def __init__(self, transfer_id: str = "", current_status: str = ""):
        self.transfer_id = transfer_id
        self.current_status = current_status
        super().__init__(
            f"Transfer {transfer_id} is not ready: "
            f"current status is {current_status}, must be DONE"
        )
