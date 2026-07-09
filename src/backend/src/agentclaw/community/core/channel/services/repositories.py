"""Channel Repository Protocol and Record.

Defines the abstract interface for channel persistence operations.
Implementations are provided in plugins/local and plugins/prod.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class ChannelRecord:
    """Database record for ac_channel_config."""

    id: int
    type: str
    description: str | None
    identity_id: str
    bind_bot_id: str
    config: dict[str, Any]
    status: str
    deleted: int
    gmt_create: datetime | None
    gmt_modified: datetime | None
    env: str
    stage: str | None


@runtime_checkable
class ChannelRepository(Protocol):
    """Protocol for channel repository implementations.

    Implementation: a single unified ORM body at
    ``plugins.channel_repository.ChannelRepository`` (runs on both
    the corp store and SQLite via the injected ``DatabasePlugin``).
    """

    def insert_channel(
        self,
        *,
        type: str,
        description: str | None,
        identity_id: str,
        bind_bot_id: str,
        config: dict[str, Any],
        status: str,
        stage: str | None = None,
    ) -> int:
        """Insert a new channel record and return its ID."""
        ...

    def get_by_type_and_identity_ids(
        self,
        *,
        type: str,
        identity_ids: list[str],
        bind_bot_id: str,
    ) -> list[ChannelRecord]:
        """Get channels by type, identity_ids and bind_bot_id (deleted=0)."""
        ...

    def get_by_id(self, channel_id: int) -> ChannelRecord | None:
        """Get channel by id."""
        ...

    def update_by_id(
        self,
        *,
        channel_id: int,
        type: str,
        description: str | None,
        identity_id: str,
        bind_bot_id: str,
        config: dict[str, Any],
        status: str,
        stage: str | None = None,
    ) -> None:
        """Update all fields of a channel record by id."""
        ...

    def update_status_by_id(self, *, channel_id: int, status: str) -> None:
        """Update status of a channel record by id."""
        ...

    def delete_by_id(self, *, channel_id: int) -> None:
        """Logical delete a channel record by id (set deleted=1)."""
        ...
