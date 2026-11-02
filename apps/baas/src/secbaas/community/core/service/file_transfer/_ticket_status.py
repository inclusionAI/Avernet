"""Ticket transition policy; repository calls each use a short session."""

from secbaas.community.core.repository.file_transfer_ticket import (
    TicketRepository,
    TransferNotFoundError,
    TransferStateConflictError,
)

# Upload path: CREATED->UPLOADING->UPLOAD_COMPLETED->PULLING->DONE
# Retention path: CREATED->UPLOADING->UPLOAD_COMPLETED->DONE (device_path IS NULL)  [Phase 69]
# Download path: CREATED->PUSHING->DONE
# Cancel path: any non-terminal upload state -> CANCELLED (terminal)  [Phase 72]
# Delete staging path: DONE/FAILED/CANCELLED -> DELETED (terminal)  [Phase 72]
# Failure: any non-terminal state -> FAILED
# Same-state: idempotent no-op
VALID_TRANSITIONS = frozenset(
    {
        ("CREATED", "UPLOADING"),
        ("UPLOADING", "UPLOAD_COMPLETED"),
        ("CREATED", "UPLOAD_COMPLETED"),  # v1.5: complete_upload + poller OSS detection
        ("UPLOAD_COMPLETED", "PULLING"),
        (
            "UPLOAD_COMPLETED",
            "DONE",
        ),  # Phase 69: retention mode shortcut (device_path IS NULL)
        ("PULLING", "DONE"),
        ("CREATED", "PUSHING"),
        ("PUSHING", "DONE"),
        # Failure from any non-terminal state
        ("CREATED", "FAILED"),
        ("UPLOADING", "FAILED"),
        ("UPLOAD_COMPLETED", "FAILED"),
        ("PULLING", "FAILED"),
        ("PUSHING", "FAILED"),
        # Phase 72: Cancel upload -- non-terminal upload states -> CANCELLED
        # (UPLOAD_COMPLETED deliberately excluded: OSS object already exists, cannot cancel)
        ("CREATED", "CANCELLED"),
        ("UPLOADING", "CANCELLED"),
        ("PULLING", "CANCELLED"),
        # Phase 72: Delete staging -- terminal states -> DELETED
        ("DONE", "DELETED"),
        ("FAILED", "DELETED"),
        ("CANCELLED", "DELETED"),
    }
)


def update_ticket_status(
    repository: TicketRepository,
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
