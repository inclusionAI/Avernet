"""Composition of the forwarding subsystem (composition root, Rule 14).

Builds the domain map and assembles the :class:`Forwarding` domain object.
Adapters receive the built ``Forwarding`` via ``app.state`` and never import
plugins or core.
"""

from __future__ import annotations

import logging
from pathlib import Path

from gateway.community.config import Config
from gateway.community.core.forwarding import (
    DomainMap,
    EngineRoute,
    Forwarding,
    build_engine_route,
)
from gateway.community.spi.forwarder import Forwarder
from gateway.community.spi.schema_catalog import SchemaCatalog
from gateway.community.spi.ws_forwarder import WebSocketForwarder

_logger = logging.getLogger("bootstrap")

_DEFAULT_REFRESH_SECONDS = 300.0


def build_forwarding(
    forwarder: Forwarder,
    catalog: SchemaCatalog,
    ws_forwarder: WebSocketForwarder,
) -> Forwarding:
    """Build the forwarding subsystem (called once from ``create_app``).

    All parameters are required — the caller must resolve every dependency
    through the DI container. Schema sources are loaded from configs and
    injected into the catalog if it supports ``set_sources``.
    """
    from gateway.community.config import ConfigLoader

    config = ConfigLoader.load()
    domain_map = _load_domain_map(config)
    engine_route = _load_engine_route(config)
    refresh_seconds = _DEFAULT_REFRESH_SECONDS
    sources: dict[str, str | Path] = {}
    if config.config_dir is not None:
        for name, domain in domain_map.domains.items():
            if domain.schema.source == "file" and domain.schema.location:
                sources[name] = config.config_dir / domain.schema.location
                refresh_seconds = float(domain.schema.refresh_seconds)
    if sources and hasattr(catalog, "set_sources"):
        catalog.set_sources(sources)
        if hasattr(catalog, "refresh_all"):
            catalog.refresh_all()
    return Forwarding(
        domain_map=domain_map,
        forwarder=forwarder,
        catalog=catalog,
        ws_forwarder=ws_forwarder,
        engine_route=engine_route,
        refresh_seconds=refresh_seconds,
    )


def _load_engine_route(config: Config) -> EngineRoute | None:
    """The root-anchored ``/engine`` socket route, if this deployment has one.

    Read from the same ``user_config.upstreams`` section the domain map is read
    from, so one section describes everything the gateway routes. Absent is a
    supported answer and the community build's: a gateway that fronts no engine
    proxy refuses every handshake on the prefix rather than serving a socket to
    nowhere.
    """
    upstreams_raw = config.user_config.upstreams
    if not isinstance(upstreams_raw, dict):
        return None
    engine_route = build_engine_route(upstreams_raw, config.user_config.upstream_vars)
    if engine_route is None:
        _logger.info(
            "no engine socket route configured (application.yaml "
            "user_config.upstreams.engine): /engine/** is not served"
        )
    else:
        _logger.info(
            "engine socket route → %s (%s)",
            engine_route.server,
            engine_route.ws_base_url,
        )
    return engine_route


def _load_domain_map(config: Config | None = None) -> DomainMap:
    if config is None:
        from gateway.community.config import ConfigLoader

        config = ConfigLoader.load()
    upstream_vars = config.user_config.upstream_vars
    upstreams_raw = config.user_config.upstreams
    if not isinstance(upstreams_raw, dict) or not upstreams_raw:
        raise ValueError(
            "required config section not found: application.yaml user_config.upstreams"
        )
    _logger.info("loading upstream config from application.yaml user_config.upstreams")
    domain_map = DomainMap.from_config(upstreams_raw, variables=upstream_vars)
    _logger.info(
        "upstream config (application.yaml user_config.upstreams): %d domains\n%s",
        len(domain_map.domains),
        "\n".join(
            f"  {name} → {domain.server}, schema={domain.schema.location}"
            for name, domain in domain_map.domains.items()
        ),
    )
    return domain_map
