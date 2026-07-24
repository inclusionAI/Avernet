"""Composition of the forwarding subsystem (composition root, Rule 14).

Builds the domain map, the forwarder, and the schema catalog, and owns the
catalog's background refresh lifecycle. Adapters receive the built
:class:`Forwarding` via ``app.state`` and never import plugins or core.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path

from gateway.community.core.authn import RouteSecurity
from gateway.community.core.forwarding import DomainMap, build_served_openapi
from gateway.community.plugins.forwarder.bare import BareForwarder
from gateway.community.plugins.schema_catalog.bare import BareSchemaCatalog
from gateway.community.spi.forwarder import Forwarder
from gateway.community.spi.schema_catalog import SchemaCatalog

_DEFAULT_REFRESH_SECONDS = 300.0


@dataclass
class Forwarding:
    """The composed forwarding subsystem, handed to the web adapter."""

    domain_map: DomainMap
    forwarder: Forwarder
    catalog: SchemaCatalog
    refresh_seconds: float = _DEFAULT_REFRESH_SECONDS
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _task: asyncio.Task[None] | None = field(default=None)

    async def start_refresh(self) -> None:
        """Begin the catalog's background refresh (no-op if unsupported)."""
        catalog = self.catalog
        if isinstance(catalog, BareSchemaCatalog) and self._task is None:
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
        return build_served_openapi(
            list(self.domain_map.domains),
            self.catalog.current,
            route_security,
            title=title,
            version=version,
            description=description,
        )


def build_forwarding() -> Forwarding:
    """Build the forwarding subsystem (called once from ``create_app``)."""
    configs_dir = _resolve_configs_dir()
    domain_map = _load_domain_map(configs_dir)
    sources: dict[str, str | Path] = {}
    refresh_seconds = _DEFAULT_REFRESH_SECONDS
    if configs_dir is not None:
        for name, domain in domain_map.domains.items():
            if domain.schema.source == "file" and domain.schema.location:
                sources[name] = configs_dir / domain.schema.location
                refresh_seconds = float(domain.schema.refresh_seconds)
    catalog = BareSchemaCatalog(sources)
    catalog.refresh_all()
    return Forwarding(
        domain_map=domain_map,
        forwarder=BareForwarder(),
        catalog=catalog,
        refresh_seconds=refresh_seconds,
    )


def _load_domain_map(configs_dir: Path | None) -> DomainMap:
    if configs_dir is not None:
        path = configs_dir / "upstreams.yaml"
        if path.exists():
            return DomainMap.from_yaml(path)
    return DomainMap()


def _resolve_configs_dir() -> Path | None:
    explicit = os.getenv("GATEWAY_CONFIG_PATH", "").strip()
    if explicit:
        p = Path(explicit)
        return p if p.is_dir() else p.parent
    cwd = Path.cwd() / "configs"
    return cwd if cwd.exists() else None
