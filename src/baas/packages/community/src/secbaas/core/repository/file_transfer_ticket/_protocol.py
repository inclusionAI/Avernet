"""Protocol for file transfer ticket persistence operations."""

from typing import Protocol, runtime_checkable

from ._record import TicketRecord


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
        """Update ticket status with transition validation.

        Two-phase: query current status -> _validate_transition -> UPDATE.
        Raises DeviceCreationError on invalid transition.
        """
        ...

    def _validate_transition(self, current: str, target: str) -> None:
        """Validate state transition. Raises DeviceCreationError on conflict."""
        ...