"""Ticket transition policy; repository calls each use a short session."""

from secbaas.community.core.repository.session_file_ticket import (
    SessionTicketRepository,
    TransferNotFoundError,
    TransferStateConflictError,
)

# Upload path:  CREATED -> UPLOADING -> DONE  (fast: CREATED -> DONE)
# Cancel path:  CREATED/UPLOADING -> CANCELLED
# Failure path: UPLOADING -> FAILED
# Delete path:  DONE/FAILED/CANCELLED -> DELETED
# Same-state:   idempotent no-op
VALID_TRANSITIONS = frozenset(
    {
        # Upload path
        ("CREATED", "UPLOADING"),
        ("UPLOADING", "DONE"),
        ("CREATED", "DONE"),  # Session complete_upload fast path (no device pull)
        # Cancel path
        ("CREATED", "CANCELLED"),
        ("UPLOADING", "CANCELLED"),
        # Failure path
        ("UPLOADING", "FAILED"),
        # Delete path (terminal states -> DELETED)
        ("DONE", "DELETED"),
        ("FAILED", "DELETED"),
        ("CANCELLED", "DELETED"),
    }
)


def update_ticket_status(
    repository: SessionTicketRepository,
    transfer_id: str,
    new_status: str,
    error_message: str | None = None,
) -> None:
    """Apply an atomic transition and explain failure after releasing its session.

    The diagnostic snapshot can observe a later concurrent transition. Never
    turn a failed CAS into an unconditional write or claim an unpersisted error
    message succeeded. Propagate DB failures, including commit errors.
    """
    expected = frozenset(
        {new_status} | {src for src, dst in VALID_TRANSITIONS if dst == new_status}
    )
    affected = repository.compare_and_set_status(
        transfer_id, new_status, error_message, expected_statuses=expected
    )
    if affected > 0:
        return
    row = repository.get_by_transfer_id(transfer_id)
    if row is None:
        raise TransferNotFoundError(transfer_id)
    if row.status == new_status and (
        error_message is None or row.error_message == error_message
    ):
        return
    raise TransferStateConflictError(
        f"Cannot transition from {row.status} to {new_status}: "
        "ticket is in a conflicting or terminal state."
    )
