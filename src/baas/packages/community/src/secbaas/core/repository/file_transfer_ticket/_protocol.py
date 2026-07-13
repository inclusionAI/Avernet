"""Protocol for file transfer ticket persistence operations."""

from typing import Protocol, runtime_checkable

from ._record import TicketRecord


class FileTransferRepositoryError(RuntimeError):
    """Base error for file transfer ticket repository operations."""

    def __init__(self, message: str, error_code: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


class TransferNotFoundError(FileTransferRepositoryError):
    """Raised when a transfer ticket is not found by transfer_id."""

    def __init__(self, transfer_id: str) -> None:
        super().__init__(
            f"Transfer ticket {transfer_id} not found",
            error_code="FILE_TRANSFER_NOT_FOUND",
        )


class TransferStateConflictError(FileTransferRepositoryError):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, message: str) -> None:
        super().__init__(message, error_code="FILE_TRANSFER_STATE_CONFLICT")


@runtime_checkable
class TicketRepository(Protocol):
    """Protocol for file transfer ticket persistence operations.

    Implementations:
    - OrmTicketRepository: SQLAlchemy ORM implementation.
    """

    def create_ticket(
        self,
        *,
        transfer_id: str,
        tenant: str,
        paas_device_id: str,
        direction: str,
        status: str,
        staging_subdir: str | None,
        filename: str,
        device_path: str | None,
        fileservice_staging_path: str,
        error_message: str | None,
    ) -> int:
        """Insert a new transfer ticket record. Returns the new record ID."""
        ...

    def list_pending_uploads(
        self, statuses: list[str], limit: int
    ) -> list[TicketRecord]:
        """List tickets matching given statuses, ordered by gmt_create ASC, limited."""
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
        Raises DeviceCreationError on invalid transition or not found.
        """
        ...

    def get_by_transfer_id(
        self, transfer_id: str, tenant: str | None = None,
    ) -> TicketRecord | None:
        """Look up a ticket by its transfer_id, optionally scoped to tenant.

        Returns None if not found.
        """
        ...

    def update_urls(
        self,
        transfer_id: str,
        *,
        download_url: str | None = None,
        upload_url: str | None = None,
    ) -> None:
        """Update download_url and/or upload_url on a ticket.

        Updates only the fields that are not None. Both None is a no-op.
        """
        ...