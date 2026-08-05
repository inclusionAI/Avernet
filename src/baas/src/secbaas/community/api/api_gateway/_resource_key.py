"""Resource key contract types — re-exported for adapter consumption.

These Protocol and dataclass definitions originate in core.repository.resource_key
but are re-exported here so that adapter layers can depend on them without
importing from core (which is banned by architecture layer rules).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class ResourceKeyRecord:
    """baas_resource_key table record."""

    id: int
    gmt_create: datetime
    gmt_modified: datetime
    tenant: str
    resource_key: str
    app: str


@runtime_checkable
class ResourceKeyRepository(Protocol):
    """Resource key repository protocol."""

    def get_by_resource_key_and_tenant(
        self, resource_key: str, tenant: str
    ) -> ResourceKeyRecord | None:
        """Look up record by resource_key and tenant."""
        ...

    def exists_bot_mapping(self, resource_key_id: int, bot_id: str) -> bool:
        """Check whether a mapping between resource_key_id and bot_id exists."""
        ...
