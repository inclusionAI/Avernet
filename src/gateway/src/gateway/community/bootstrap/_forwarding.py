"""Composition of the forwarding subsystem (composition root, Rule 14).

Builds the domain map and assembles the :class:`Forwarding` domain object.
Adapters receive the built ``Forwarding`` via ``app.state`` and never import
plugins or core.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from gateway.community.config import Config
from gateway.community.core.forwarding import DomainMap, Forwarding
from gateway.community.spi.forwarder import Forwarder
from gateway.community.spi.schema_catalog import SchemaCatalog
from gateway.community.spi.ws_forwarder import WebSocketForwarder

_logger = logging.getLogger("bootstrap")

_DEFAULT_REFRESH_SECONDS = 300.0


def build_forwarding(
    forwarder: Forwarder,
    schema_catalogs: Mapping[str, SchemaCatalog],
    ws_forwarder: WebSocketForwarder,
) -> Forwarding:
    from gateway.community.config import ConfigLoader

    file_cat = schema_catalogs["file"]
    http_cat = schema_catalogs["http"]

    config = ConfigLoader.load()
    domain_map = _load_domain_map(config)

    file_sources: dict[str, Path] = {}
    http_sources: dict[str, str] = {}
    refresh_seconds = _DEFAULT_REFRESH_SECONDS

    if config.config_dir is not None:
        for name, domain in domain_map.domains.items():
            if not domain.schema.location:
                continue
            if domain.schema.source == "file":
                file_sources[name] = config.config_dir / domain.schema.location
                refresh_seconds = float(domain.schema.refresh_seconds)
            elif domain.schema.source == "http":
                http_sources[name] = domain.schema.location
                refresh_seconds = float(domain.schema.refresh_seconds)

    if file_sources:
        file_cat.set_sources(file_sources)
        file_cat.refresh_all()
    if http_sources:
        http_cat.set_sources(http_sources)
        http_cat.refresh_all()

    return Forwarding(
        domain_map=domain_map,
        forwarder=forwarder,
        schema_catalogs=schema_catalogs,
        ws_forwarder=ws_forwarder,
        refresh_seconds=refresh_seconds,
    )


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
