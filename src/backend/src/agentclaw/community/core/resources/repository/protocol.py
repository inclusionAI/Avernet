"""Resource repository protocol.

The repository speaks in plain dicts (the `Resource.to_dict()` format) so
plugin implementations can store arbitrary `attributes` without coupling
to pydantic model evolution. The service layer converts to/from the new
`Resource` pydantic model.
"""
from __future__ import annotations

from typing import List, Optional, Protocol


class ResourceRepositoryProtocol(Protocol):
    """Persistent storage for resources (files, URLs, nodes)."""

    def get_by_id(self, resource_id: str) -> Optional[dict]:
        """Fetch a single resource by primary key, or None."""
        ...

    def get_by_path(self, path: str, bolt_id: Optional[str] = None) -> Optional[dict]:
        """Fetch a FILE resource by its stored filesystem path."""
        ...

    def list_resources(
        self,
        resource_type: Optional[str] = None,
        parent_path: Optional[str] = None,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        bolt_id: Optional[str] = None,
    ) -> List[dict]:
        """List resources matching the given filters."""
        ...

    def create(self, resource_data: dict) -> dict:
        """Insert a new resource and return the stored representation."""
        ...

    def update(self, resource_id: str, resource_data: dict) -> Optional[dict]:
        """Update fields on an existing resource, or None if missing."""
        ...

    def delete(self, resource_id: str) -> bool:
        """Soft-delete (status=deleted). Returns True if a row was updated."""
        ...

    def hard_delete(self, resource_id: str) -> bool:
        """Physically remove the row."""
        ...

    def count_resources(
        self,
        resource_type: Optional[str] = None,
        parent_path: Optional[str] = None,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        bolt_id: Optional[str] = None,
    ) -> int:
        """Count resources matching the given filters."""
        ...
