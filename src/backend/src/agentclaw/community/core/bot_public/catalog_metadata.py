"""Transport-neutral contract for public Bot catalog membership metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class BotCatalogAddress:
    """Tenant-scoped Backend identity of one catalog Bot."""

    bot_id: str
    entity_id: str


@dataclass(frozen=True)
class BotCatalogCaller:
    """Trusted principal context supplied to the catalog metadata port."""

    tenant_id: str
    user_id: str | None
    app_id: int | None


@dataclass(frozen=True)
class BotCatalogMetadata:
    """Membership-only metadata returned by the catalog metadata port."""

    address: BotCatalogAddress
    kind: str


class BotCatalogMetadataUnavailableError(Exception):
    """The catalog metadata port cannot make an authoritative decision."""


@runtime_checkable
class BotCatalogMetadataServiceProtocol(Protocol):
    """Authoritative BCS page lookup for tenant-scoped catalog Bots."""

    def search_public_bot_metadata(
        self,
        *,
        search: str | None,
        page: int,
        page_size: int,
        caller: BotCatalogCaller,
        request_id: str,
    ) -> Sequence[BotCatalogMetadata]: ...


class BotCatalogSearchUnavailableError(Exception):
    """The BCS-backed catalog metadata source is unavailable."""
