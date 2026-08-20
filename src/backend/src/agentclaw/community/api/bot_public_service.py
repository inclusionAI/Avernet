"""Service API Protocol for public-bot lifecycle + friend approvals."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Protocol, Sequence, runtime_checkable


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
    """Authoritative membership lookup for tenant-scoped catalog Bots."""

    def query_public_bot_metadata(
        self,
        *,
        addresses: Sequence[BotCatalogAddress],
        caller: BotCatalogCaller,
        request_id: str,
    ) -> Sequence[BotCatalogMetadata]: ...


class BotCatalogSearchUnavailableError(Exception):
    """The BCS-backed catalog metadata source is unavailable."""


@runtime_checkable
class BotPublicServiceProtocol(Protocol):
    """Service API for bot publishing + friend-request workflow."""

    def public_bot(self, *args: Any, **kwargs: Any) -> Dict[str, Any]: ...

    def handle_public_approval_callback(self, *args: Any, **kwargs: Any) -> Dict[str, Any]: ...

    def create_friend_request_approval(self, *args: Any, **kwargs: Any) -> Any: ...

    def handle_friend_request_approval_callback(self, *args: Any, **kwargs: Any) -> Any: ...

    def search_public_bots_by_keyword(self, *args: Any, **kwargs: Any) -> Any: ...

    def search_catalog_public_bots_by_keyword(self, *args: Any, **kwargs: Any) -> Any: ...

    def list_my_bot_friends(self, *args: Any, **kwargs: Any) -> Any: ...

    def get_friend_record(self, *args: Any, **kwargs: Any) -> Any: ...
