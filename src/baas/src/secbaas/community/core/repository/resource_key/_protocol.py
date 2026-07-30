from typing import Protocol, runtime_checkable

from ._record import ResourceKeyRecord


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