"""
Session file transfer status enumeration.
"""

from enum import StrEnum


class TransferStatus(StrEnum):
    """Transfer ticket status enumeration (6-state machine).

    Upload path:  CREATED -> UPLOADING -> DONE
    Cancel path:  CREATED/UPLOADING -> CANCELLED
    Delete path:  DONE/FAILED/CANCELLED -> DELETED
    Failure path: UPLOADING -> FAILED
    """

    CREATED = "CREATED"
    UPLOADING = "UPLOADING"
    CANCELLED = "CANCELLED"
    DELETED = "DELETED"
    DONE = "DONE"
    FAILED = "FAILED"


# Transition graph for the 6-state machine:
#
#   CREATED --► UPLOADING --► DONE --► DELETED
#      │            │
#      │            ├──► FAILED --► DELETED
#      │            │
#      └──► CANCELLED --► DELETED
#
# Upload path:  CREATED -> UPLOADING -> DONE
# Cancel path:  CREATED/UPLOADING -> CANCELLED
# Failure path: UPLOADING -> FAILED
# Delete path:  DONE/FAILED/CANCELLED -> DELETED
# Same-state:   idempotent no-op (handled by update_status CAS logic)
#
# VALID_TRANSITIONS is defined in _orm_repository.py (single source of truth).