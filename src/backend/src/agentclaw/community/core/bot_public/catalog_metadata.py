"""Transport-neutral contract for public Bot catalog membership metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence, runtime_checkable


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
class BotCatalogSearchFilters:
    """Validated optional BCS Catalog Search filters."""

    visibility: tuple[str, ...] = ()
    user_visibility: tuple[str, ...] = ()
    status: str | None = None
    viewer_actor_type: str | None = None
    viewer_actor_id: str | None = None
    friendship: str | None = None


@dataclass(frozen=True)
class BotCatalogMetadata:
    """BCS metadata retained for one catalog Bot after transport validation."""

    address: BotCatalogAddress
    kind: str
    bot_uuid: str | None = None
    is_friend: bool | None = None
    visibility: Any = None
    is_online: Any = None
    actor_kind: str | None = None
    friend_ext: Any = None
    friend_check_in_strategy: Any = None
    user_visibility: Any = None


@dataclass(frozen=True)
class BotCatalogMetadataPage:
    """One BCS page, retaining BCS pagination metadata for the public result."""

    total: int
    items: Sequence[BotCatalogMetadata]


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
        bot_uuids: Sequence[str] = (),
        filters: BotCatalogSearchFilters | None,
        caller: BotCatalogCaller,
        request_id: str,
    ) -> BotCatalogMetadataPage: ...


class BotCatalogSearchUnavailableError(Exception):
    """The BCS-backed catalog metadata source is unavailable."""
