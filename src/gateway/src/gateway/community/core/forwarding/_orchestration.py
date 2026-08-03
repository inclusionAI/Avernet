"""Forwarding subsystem orchestration — transport-agnostic domain class.

``Forwarding`` composes the domain map, both forwarders, and the schema catalog,
and owns the catalog's background refresh lifecycle. It is a domain object, not
bootstrap wiring — adapters receive it via ``app.state`` and never import
plugins or core.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from gateway.community.core.authn import RouteSecurity
from gateway.community.core.forwarding import DomainMap, build_served_openapi
from gateway.community.spi.forwarder import Forwarder
from gateway.community.spi.schema_catalog import SchemaCatalog
from gateway.community.spi.ws_forwarder import WebSocketForwarder

_DEFAULT_REFRESH_SECONDS = 300.0


@dataclass
class Forwarding:
    """The composed forwarding subsystem, handed to the web adapter."""

    domain_map: DomainMap
    forwarder: Forwarder
    catalog: SchemaCatalog
    ws_forwarder: WebSocketForwarder
    refresh_seconds: float = _DEFAULT_REFRESH_SECONDS
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _task: asyncio.Task[None] | None = field(default=None)

    async def start_refresh(self) -> None:
        """Begin the catalog's background refresh (no-op if unsupported)."""
        catalog = self.catalog
        if hasattr(catalog, "refresh_loop") and self._task is None:
            self._task = asyncio.create_task(
                catalog.refresh_loop(self.refresh_seconds, self._stop)
            )

    async def stop_refresh(self) -> None:
        """Stop the background refresh and await the task."""
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    def served_openapi(
        self,
        route_security: RouteSecurity,
        *,
        title: str,
        version: str,
        description: str = "",
    ) -> dict[str, object]:
        """The current served OpenAPI document across all configured domains."""
        rewrites = {
            name: domain.rewrite for name, domain in self.domain_map.domains.items()
        }
        return build_served_openapi(
            list(self.domain_map.domains),
            self.catalog.current,
            route_security,
            title=title,
            version=version,
            description=description,
            rewrites=rewrites,
        )
