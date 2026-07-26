"""Domain → upstream-server resolution (transport-agnostic).

The gateway routes by **domain**: the leading path segment after the version
base (e.g. ``bots`` in ``/openapi/v1/bots/...``) selects the target server. The
map is loaded from ``upstreams.yaml``; a request whose leading segment matches no
configured domain resolves to ``None`` (the caller denies — never an open proxy).

No web framework here (Rule 7): this is pure resolution logic.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ENV_REF = re.compile(r"\$\{([^}]+)\}")
_DEFAULT_BASE_PATH = "/openapi/v1"
_DEFAULT_REFRESH_SECONDS = 300


def _expand_env(value: str) -> str:
    """Expand ``${VAR}`` references from the environment (unset → empty)."""
    return _ENV_REF.sub(lambda m: os.getenv(m.group(1), ""), value)


@dataclass(frozen=True)
class Server:
    """A resolved upstream target."""

    name: str
    base_url: str


@dataclass(frozen=True)
class SchemaSource:
    """Where a domain's published OpenAPI description is read from."""

    source: str  # "file" (single-box) | "object_store" (any deployed edition)
    location: str  # file path or object-store URL
    refresh_seconds: int = _DEFAULT_REFRESH_SECONDS


@dataclass(frozen=True)
class Domain:
    """A configured domain: its server and its schema source."""

    name: str
    server: Server
    schema: SchemaSource


@dataclass(frozen=True)
class DomainMap:
    """The compiled domain → server map, queryable per request."""

    base_path: str = _DEFAULT_BASE_PATH
    domains: dict[str, Domain] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path) -> DomainMap:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        if not isinstance(raw, dict):
            raw = {}
        return cls.from_config(raw)

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> DomainMap:
        base_path = str(raw.get("base_path", _DEFAULT_BASE_PATH))
        servers = _parse_servers(raw.get("servers") or {})
        domains = _parse_domains(raw.get("domains") or {}, servers)
        return cls(base_path=base_path, domains=domains)

    def resolve(self, path: str) -> Server | None:
        """The server for *path*'s domain, or ``None`` if the domain is unknown."""
        domain = self.domain_for(path)
        return domain.server if domain is not None else None

    def domain_for(self, path: str) -> Domain | None:
        """The domain *path* belongs to, or ``None`` if outside the version base."""
        base = _segments(self.base_path)
        segments = _segments(path)
        if segments[: len(base)] != base:
            return None
        rest = segments[len(base) :]
        if not rest:
            return None
        return self.domains.get(rest[0])


def _segments(path: str) -> list[str]:
    return [seg for seg in path.split("/") if seg]


def _parse_servers(raw: dict[str, Any]) -> dict[str, Server]:
    servers: dict[str, Server] = {}
    for name, spec in raw.items():
        spec = spec or {}
        servers[name] = Server(
            name=name, base_url=_expand_env(str(spec.get("base_url", "")))
        )
    return servers


def _parse_domains(
    raw: dict[str, Any], servers: dict[str, Server]
) -> dict[str, Domain]:
    domains: dict[str, Domain] = {}
    for name, spec in raw.items():
        spec = spec or {}
        server_name = str(spec.get("server", ""))
        server = servers.get(server_name)
        if server is None:
            raise ValueError(
                f"domain {name!r} references unknown server {server_name!r}"
            )
        domains[name] = Domain(
            name=name, server=server, schema=_parse_schema(spec.get("schema") or {})
        )
    return domains


def _parse_schema(raw: dict[str, Any]) -> SchemaSource:
    source = str(raw.get("source", "file"))
    location = str(raw.get("path") or raw.get("url") or "")
    refresh = int(raw.get("refresh_seconds", _DEFAULT_REFRESH_SECONDS))
    return SchemaSource(source=source, location=location, refresh_seconds=refresh)
