"""Value types for the Engine-owned session file data plane."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class SessionFileError(ValueError):
    """An Engine session file could not be safely accessed."""


@dataclass(frozen=True)
class SessionFileView:
    resource_id: str
    display_name: str
    size_bytes: int
    availability: str

    def as_dict(self) -> dict[str, str | int]:
        return {
            "resource_id": self.resource_id,
            "display_name": self.display_name,
            "size_bytes": self.size_bytes,
            "availability": self.availability,
        }


@dataclass(frozen=True)
class SessionFileExportSource:
    """A manifest-controlled file prepared for an external download."""

    resource_id: str
    session_key_hash: str
    filename: str
    size_bytes: int
    content_hash: str
    canonical_path: Path
    tenant: str
    transfer_id: str
    requires_upload: bool


@dataclass(frozen=True)
class SessionFileTransferRequest:
    resource_id: str
    tenant: str
    session_key: str
    transfer_id: str


@dataclass(frozen=True)
class SessionFileUploadGrant:
    transfer_id: str
    upload_type: str
    upload_url: str | None = None
    http_method: str = "PUT"
    part_size: int | None = None
    part_count: int | None = None
    parts: list[dict] | None = None


@dataclass(frozen=True)
class BaasFileExportShareLink:
    download_url: str
    expires_at: str


@dataclass(frozen=True)
class SessionFileExternalDownload:
    download_url: str
    expires_at: str
    filename: str
    size_bytes: int
