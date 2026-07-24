"""Protocol for session file ticket persistence operations."""

from typing import Protocol, runtime_checkable

from secbaas.api.bot_runtime import (
    TransferNotFoundError as ApiTransferNotFoundError,
)
from secbaas.api.bot_runtime import (
    TransferStateConflictError as ApiTransferStateConflictError,
)

from ._record import SessionTicketRecord


class FileTransferRepositoryError(RuntimeError):
    """Base error for file transfer ticket repository operations."""

    def __init__(self, message: str, error_code: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


class TransferNotFoundError(FileTransferRepositoryError, ApiTransferNotFoundError):
    """Raised when a transfer ticket is not found by transfer_id.

    Inherits from both FileTransferRepositoryError (repo-layer base) and
    the API-layer TransferNotFoundError so that except clauses catching
    the API class also handle repo-originated instances (e.g., from
    update_status in race conditions).
    """

    def __init__(self, transfer_id: str) -> None:
        message = f"Transfer ticket {transfer_id} not found"
        self.message = (
            message  # Set explicitly -- cooperative MRO stops at RuntimeError
        )
        super().__init__(
            message,
            error_code="FILE_TRANSFER_NOT_FOUND",
        )


class TransferStateConflictError(
    FileTransferRepositoryError, ApiTransferStateConflictError
):
    """Raised when an invalid state transition is attempted on a file transfer.

    Inherits from both FileTransferRepositoryError (repo-layer base) and
    the API-layer TransferStateConflictError so that except clauses catching
    the API class also handle repo-originated instances.
    """

    def __init__(self, message: str) -> None:
        self.message = (
            message  # Set explicitly -- BotServiceError.__init__ unreachable via MRO
        )
        super().__init__(message, error_code="FILE_TRANSFER_STATE_CONFLICT")


@runtime_checkable
class SessionTicketRepository(Protocol):
    """Protocol for session file ticket persistence operations.

    Implementations:
    - OrmSessionTicketRepository: SQLAlchemy ORM implementation.
    """

    def create_ticket(
        self,
        *,
        transfer_id: str,
        tenant: str,
        session_id: str,
        status: str,
        staging_subdir: str | None,
        filename: str,
        fileservice_staging_path: str,
        error_message: str | None,
        multipart_session_id: str | None = None,
        operator: str = "unknown",
    ) -> int:
        """Insert a new session transfer ticket record. Returns the new record ID."""
        ...

    def list_by_session(
        self, tenant: str, session_id: str
    ) -> list[SessionTicketRecord]:
        """List tickets for a given tenant + session_id, ordered by gmt_create DESC."""
        ...

    def update_status(
        self,
        transfer_id: str,
        new_status: str,
        error_message: str | None = None,
    ) -> None:
        """Update ticket status with CAS-style atomic transition validation.

        Uses a CAS (Compare-And-Swap) SQL UPDATE: only modifies the row if
        its current status is one of the allowed source states for new_status.
        Same-state transitions are idempotent.
        Raises TransferStateConflictError on invalid state transition.
        Raises TransferNotFoundError if no ticket with the given transfer_id exists.
        """
        ...

    def get_by_transfer_id(
        self,
        transfer_id: str,
        tenant: str | None = None,
    ) -> SessionTicketRecord | None:
        """Look up a ticket by its transfer_id, optionally scoped to tenant.

        Returns None if not found.
        """
        ...
