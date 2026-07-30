"""Composition of the forwarding subsystem (composition root, Rule 14).

Builds the domain map and assembles the :class:`Forwarding` domain object.
Adapters receive the built ``Forwarding`` via ``app.state`` and never import
plugins or core.
"""

from __future__ import annotations

import logging
from pathlib import Path

from gateway.community.core.forwarding import DomainMap, Forwarding
from gateway.community.spi.forwarder import Forwarder
from gateway.community.spi.schema_catalog import SchemaCatalog

_logger = logging.getLogger("bootstrap")

_DEFAULT_REFRESH_SECONDS = 300.0


def build_forwarding(
    forwarder: Forwarder,
    catalog: SchemaCatalog,
) -> Forwarding:
    """Build the forwarding subsystem (called once from ``create_app``).

    All parameters are required — the caller must resolve every dependency
    through the DI container. Schema sources are loaded from configs and
    injected into the catalog if it supports ``set_sources``.
    """
    configs_dir = _resolve_configs_dir()
    domain_map = _load_domain_map(configs_dir)
    refresh_seconds = _DEFAULT_REFRESH_SECONDS
    sources: dict[str, str | Path] = {}
    if configs_dir is not None:
        for name, domain in domain_map.domains.items():
            if domain.schema.source == "file" and domain.schema.location:
                sources[name] = configs_dir / domain.schema.location
                refresh_seconds = float(domain.schema.refresh_seconds)
    if sources and hasattr(catalog, "set_sources"):
        catalog.set_sources(sources)
        if hasattr(catalog, "refresh_all"):
            catalog.refresh_all()
    return Forwarding(
        domain_map=domain_map,
        forwarder=forwarder,
        catalog=catalog,
        refresh_seconds=refresh_seconds,
    )


def _load_domain_map(configs_dir: Path | None) -> DomainMap:
    if configs_dir is None:
        raise FileNotFoundError("configs directory not found — set GATEWAY_CONFIG_PATH")
    path = configs_dir / "upstreams.yaml"
    if not path.exists():
        raise FileNotFoundError(f"required config file not found: {path}")
    from gateway.community.config import ConfigLoader

    config = ConfigLoader.load()
    upstream_vars = config.user_config.model_dump().get("upstreams", {})
    if not isinstance(upstream_vars, dict):
        upstream_vars = {}
    _logger.info("loading upstream config from %s", path)
    domain_map = DomainMap.from_yaml(path, variables=upstream_vars)
    _logger.info(
        "upstream config (%s): %d domains\n%s",
        path,
        len(domain_map.domains),
        "\n".join(
            f"  {name} → {domain.server}, schema={domain.schema.location}"
            for name, domain in domain_map.domains.items()
        ),
    )
    return domain_map


def _resolve_configs_dir() -> Path | None:
    from ._configs import resolve_configs_dir as _rcd

    return _rcd()
