"""Database record for baas_session_file_tickets table."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class SessionTicketRecord:
    """Database record for baas_session_file_tickets table.

    Columns per DDL schema (14 fields):
    id, gmt_create, gmt_modified, transfer_id, tenant, session_id,
    status, staging_subdir, filename, fileservice_staging_path,
    error_message, multipart_session_id, env, operator

    Nullable fields: staging_subdir, error_message, multipart_session_id.
    """

    id: int
    gmt_create: datetime
    gmt_modified: datetime
    transfer_id: str
    tenant: str
    session_id: str  # replaces paas_device_id
    status: str
    staging_subdir: str | None
    filename: str
    fileservice_staging_path: str
    error_message: str | None
    multipart_session_id: str | None
    env: str
    operator: str
