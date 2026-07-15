"""
File transfer status and direction enumerations.
"""

from enum import StrEnum


class TransferDirection(StrEnum):
    """Transfer direction enumeration."""

    UPLOAD = "UPLOAD"
    DOWNLOAD = "DOWNLOAD"


class TransferStatus(StrEnum):
    """Transfer ticket status enumeration (9-state machine).

    Upload path:  CREATED -> UPLOADING -> UPLOAD_COMPLETED -> PULLING -> DONE
    Download path: CREATED -> PUSHING -> DONE
    Retention path: CREATED -> UPLOAD_COMPLETED -> DONE (device_path IS NULL)
    Cancel: CREATED/UPLOADING/UPLOAD_COMPLETED -> CANCELLED
    Delete staging: DONE/FAILED/CANCELLED -> DELETED
    Failure: any non-terminal state -> FAILED
    """

    CREATED = "CREATED"
    UPLOADING = "UPLOADING"
    UPLOAD_COMPLETED = "UPLOAD_COMPLETED"
    PULLING = "PULLING"
    PUSHING = "PUSHING"
    CANCELLED = "CANCELLED"
    DELETED = "DELETED"
    DONE = "DONE"
    FAILED = "FAILED"