"""
File transfer status and direction enumerations.
"""

from enum import StrEnum


class TransferDirection(StrEnum):
    """Transfer direction enumeration."""

    UPLOAD = "UPLOAD"
    DOWNLOAD = "DOWNLOAD"


class TransferStatus(StrEnum):
    """Transfer ticket status enumeration (7-state machine).

    Upload path:  CREATED -> UPLOADING -> UPLOAD_COMPLETED -> PULLING -> DONE
    Download path: CREATED -> PUSHING -> DONE
    Failure: any non-terminal state -> FAILED
    """

    CREATED = "CREATED"
    UPLOADING = "UPLOADING"
    UPLOAD_COMPLETED = "UPLOAD_COMPLETED"
    PULLING = "PULLING"
    PUSHING = "PUSHING"
    DONE = "DONE"
    FAILED = "FAILED"