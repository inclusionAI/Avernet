"""Value types for the Engine-owned session file data plane."""
from __future__ import annotations

from dataclasses import dataclass


class SessionFileError(ValueError):
    """An Engine session file could not be safely accessed."""


@dataclass(frozen=True)
class SessionFileView:
    resource_id: str
    display_name: str
    size_bytes: int
    availability: str
    uploaded_at: str | None

    def as_dict(self) -> dict[str, str | int | None]:
        return {
            "resource_id": self.resource_id,
            "display_name": self.display_name,
            "size_bytes": self.size_bytes,
            "availability": self.availability,
            "uploaded_at": self.uploaded_at,
        }
