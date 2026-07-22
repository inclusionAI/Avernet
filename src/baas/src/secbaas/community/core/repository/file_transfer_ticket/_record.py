"""Database record for baas_file_transfer_tickets table."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class TicketRecord:
    """Database record for baas_file_transfer_tickets table.

    Columns per DDL schema (18 fields):
    id, gmt_create, gmt_modified, transfer_id, tenant, paas_device_id,
    direction, status, staging_subdir, filename, device_path,
    fileservice_staging_path, error_message, download_url, upload_url,
    multipart_session_id, env, operator

    Nullable fields: staging_subdir, device_path, error_message, download_url,
    upload_url, multipart_session_id.
    """

    id: int
    gmt_create: datetime
    gmt_modified: datetime
    transfer_id: str
    tenant: str
    paas_device_id: str
    direction: str
    status: str
    staging_subdir: str | None
    filename: str
    device_path: str | None
    fileservice_staging_path: str
    error_message: str | None
    download_url: str | None
    upload_url: str | None
    multipart_session_id: str | None
    env: str
    operator: str
