"""Domain → upstream-server resolution (transport-agnostic).

The gateway routes by **domain**: the leading path segment after the version
base (e.g. ``bots`` in ``/openapi/v1/bots/...``) selects the target server. The
map is loaded from the ``user_config.upstreams`` section in
``application.yaml``; a request whose leading segment matches no configured
domain resolves to ``None`` (the caller denies — never an open proxy).

A domain also declares two things about *how* it is served:

- **which protocols it answers.** Request/response and relayed sockets have
  different handling and different forwarders, so a domain says which plane it
  belongs to and the entrypoint for the other plane refuses it. Unset means
  ``http``, which is what every domain was before this existed.
- **whether its path is rewritten.** By default only the origin changes and the
  path travels verbatim — the property the whole forwarding design rests on. A
  domain whose upstream serves the same resources under a different prefix may
  declare one prefix substitution instead.

No web framework here (Rule 7): this is pure resolution logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml

_ENV_REF = re.compile(r"\$\{([^}]+)\}")
_DEFAULT_BASE_PATH = "/openapi/v1"
_DEFAULT_REFRESH_SECONDS = 300

#: The request/response plane — every HTTP verb, served by the catch-all.
HTTP = "http"

#: The relayed-socket plane, served by the WebSocket entrypoint.
WEBSOCKET = "websocket"

_PROTOCOLS = frozenset({HTTP, WEBSOCKET})

#: What a domain answers when it does not say. Every domain predating the
#: protocol declaration was HTTP-only, so this keeps them byte-identical.
_DEFAULT_PROTOCOLS = frozenset({HTTP})

#: Every scheme an upstream may be configured with, and whether it means TLS.
#:
#: One standard for every server: a ``base_url`` carries a scheme, always. What
#: the scheme actually encodes is the origin plus "TLS or not" — ``https`` and
#: ``wss`` say the same thing about the connection, they just spell it for
#: different planes. So the router stores what was configured and re-spells it
#: per plane, rather than each plane requiring its own vocabulary and an
#: operator having to know which one this particular upstream serves.
_SCHEME_IS_SECURE = {"http": False, "https": True, "ws": False, "wss": True}


def _expand_vars(value: str, variables: dict[str, str]) -> str:
    """Expand ``${VAR}`` references from *variables* (missing key → empty)."""
    return _ENV_REF.sub(lambda m: variables.get(m.group(1), ""), value)


@dataclass(frozen=True)
class Server:
    """A resolved upstream target, addressable on either plane.

    ``base_url`` must carry a scheme — ``http``, ``https``, ``ws`` or ``wss`` —
    and it is validated here rather than at the point of use, so a bad value
    fails the boot with the server named instead of producing a broken request
    URL at the first call. (A scheme-less value is not merely untidy: the
    forwarder concatenates it with the path, and ``backend.example/openapi/v1``
    parses as a *relative* URL with an empty host.)

    Which of the four is written does not decide which planes the upstream can
    serve; the domain does that. The scheme only says TLS or not, and
    :attr:`http_base_url` / :attr:`websocket_base_url` spell that for whichever
    plane is asking.
    """

    name: str
    base_url: str

    def __post_init__(self) -> None:
        scheme, separator, rest = self.base_url.partition("://")
        if not separator or scheme.lower() not in _SCHEME_IS_SECURE or not rest:
            raise ValueError(
                f"upstream server {self.name!r}: base_url {self.base_url!r} must "
                f"carry a scheme (one of {sorted(_SCHEME_IS_SECURE)}) — a value "
                f"without one is a relative URL with no host"
            )

    @property
    def _secure(self) -> bool:
        return _SCHEME_IS_SECURE[self.base_url.partition("://")[0].lower()]

    @property
    def _origin(self) -> str:
        return self.base_url.partition("://")[2].rstrip("/")

    @property
    def http_base_url(self) -> str:
        """The origin spelled for the request/response plane."""
        return f"{'https' if self._secure else 'http'}://{self._origin}"

    @property
    def websocket_base_url(self) -> str:
        """The origin spelled for the socket plane."""
        return f"{'wss' if self._secure else 'ws'}://{self._origin}"


@dataclass(frozen=True)
class PathRewrite:
    """One prefix substitution applied to a forwarded path.

    Forwarding is verbatim by default: the gateway swaps the origin and sends
    the path exactly as it arrived. A rewrite is the declared exception, for an
    upstream that serves the same resources under a different prefix — the
    engine proxy publishes ``/proxypass/{target}{path}`` where the gateway
    publishes ``/openapi/v1/engine/{target}{path}``.

    Only the prefix changes. Everything past it is carried through untouched, so
    a percent-encoded segment reaches the upstream exactly as its author wrote
    it.
    """

    from_prefix: str
    to_prefix: str

    def apply(self, path: str) -> str:
        """*path* with ``from_prefix`` replaced by ``to_prefix``.

        A path that does not carry the prefix is returned unchanged. That cannot
        happen for a path the domain map has already resolved to this domain —
        :func:`_parse_rewrite` refuses a ``from`` that does not begin at the
        domain's own prefix — so it is a defensive branch, not a supported one.
        """
        if not path.startswith(self.from_prefix):
            return path
        return f"{self.to_prefix}{path[len(self.from_prefix) :]}"


@dataclass(frozen=True)
class SchemaSource:
    """Where a domain's published OpenAPI description is read from."""

    source: str  # "file" (single-box) | "object_store" (any deployed edition)
    location: str  # file path or object-store URL
    refresh_seconds: int = _DEFAULT_REFRESH_SECONDS


@dataclass(frozen=True)
class Domain:
    """A configured domain: its server, schema source, protocols and rewrite."""

    name: str
    server: Server
    schema: SchemaSource
    protocols: frozenset[str] = _DEFAULT_PROTOCOLS
    #: ``None`` when the path forwards verbatim, which is the default and the
    #: case for every domain that does not declare otherwise.
    rewrite: PathRewrite | None = None

    @property
    def serves_http(self) -> bool:
        """Whether the request/response entrypoint should serve this domain.

        A predicate rather than a protocol-name argument because the delivery
        adapters are the callers, and they may not import core (layer rule) —
        a shared string constant would have to be duplicated across that
        boundary, where it could drift.
        """
        return HTTP in self.protocols

    @property
    def serves_websocket(self) -> bool:
        """Whether the socket entrypoint should serve this domain."""
        return WEBSOCKET in self.protocols

    def upstream_path(self, path: str) -> str:
        """*path* as the upstream should see it — rewritten only if declared."""
        return path if self.rewrite is None else self.rewrite.apply(path)


@dataclass(frozen=True)
class DomainMap:
    """The compiled domain → server map, queryable per request."""

    base_path: str = _DEFAULT_BASE_PATH
    domains: dict[str, Domain] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path, variables: dict[str, str]) -> DomainMap:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        if not isinstance(raw, dict):
            raw = {}
        user_config = raw.get("user_config", {})
        raw = user_config.get("upstreams", {}) if isinstance(user_config, dict) else {}
        return cls.from_config(raw, variables=variables)

    @classmethod
    def from_config(cls, raw: dict[str, Any], variables: dict[str, str]) -> DomainMap:
        base_path = str(raw.get("base_path", _DEFAULT_BASE_PATH))
        servers = _parse_servers(raw.get("servers") or {}, variables)
        domains = _parse_domains(raw.get("domains") or {}, servers, base_path)
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

    def websocket_domains(self) -> dict[str, Domain]:
        """Every domain answering the socket plane, keyed by name.

        The web adapter uses this to mount one socket entrypoint per such
        domain, so no code has to name a particular domain — ``engine`` is
        configuration, not an identifier the gateway knows.
        """
        return {
            name: domain
            for name, domain in self.domains.items()
            if domain.serves_websocket
        }


def _segments(path: str) -> list[str]:
    return [seg for seg in path.split("/") if seg]


def _parse_servers(raw: dict[str, Any], variables: dict[str, str]) -> dict[str, Server]:
    servers: dict[str, Server] = {}
    for name, spec in raw.items():
        spec = spec or {}
        raw_base_url = str(spec.get("base_url", ""))
        base_url = _expand_vars(raw_base_url, variables)
        if not base_url:
            raise ValueError(
                f"upstream server {name!r}: base_url {raw_base_url!r} resolved to "
                f"empty — add '{_var_name(raw_base_url)}' to application.yaml "
                f"user_config.upstream_vars"
            )
        # Server validates the scheme itself, so every upstream is held to the
        # same standard however it was constructed.
        servers[name] = Server(name=name, base_url=base_url)
    return servers


def _var_name(template: str) -> str:
    """Extract ``${VAR}`` name from a template string."""
    m = _ENV_REF.search(template)
    return m.group(1) if m else template


def _parse_domains(
    raw: dict[str, Any], servers: dict[str, Server], base_path: str
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
        domain_prefix = f"{base_path.rstrip('/')}/{name}"
        domains[name] = Domain(
            name=name,
            server=server,
            schema=_parse_schema(spec.get("schema") or {}),
            protocols=_parse_protocols(name, spec.get("protocols")),
            rewrite=_parse_rewrite(name, spec.get("rewrite"), domain_prefix),
        )
    return domains


def _parse_protocols(name: str, raw: Any) -> frozenset[str]:
    if raw is None:
        return _DEFAULT_PROTOCOLS
    if isinstance(raw, str):
        return _validated_protocols(name, frozenset({raw}))
    if not isinstance(raw, list) or not raw:
        raise ValueError(
            f"domain {name!r}: protocols must be a non-empty list, got {raw!r}"
        )
    declared = cast("list[object]", raw)
    return _validated_protocols(name, frozenset(str(item) for item in declared))


def _validated_protocols(name: str, protocols: frozenset[str]) -> frozenset[str]:
    unknown = protocols - _PROTOCOLS
    if unknown:
        raise ValueError(
            f"domain {name!r}: unknown protocol(s) {sorted(unknown)} "
            f"(expected any of {sorted(_PROTOCOLS)})"
        )
    return protocols


def _parse_rewrite(name: str, raw: Any, domain_prefix: str) -> PathRewrite | None:
    """The domain's declared prefix substitution, or ``None`` for verbatim.

    ``from`` must begin at the domain's own prefix. A rewrite anchored anywhere
    else could never fire — the domain map only routes paths under that
    prefix — so it is a configuration mistake worth refusing at startup rather
    than a rule that silently never matches.
    """
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise ValueError(
            f"domain {name!r}: rewrite must be a mapping with 'from' and 'to'"
        )
    spec = cast(dict[str, Any], raw)
    from_prefix = str(spec.get("from", ""))
    to_prefix = str(spec.get("to", ""))
    if not from_prefix or not to_prefix:
        raise ValueError(f"domain {name!r}: rewrite needs both 'from' and 'to'")
    if not from_prefix.startswith(domain_prefix):
        raise ValueError(
            f"domain {name!r}: rewrite.from {from_prefix!r} must start with "
            f"{domain_prefix!r} — a rewrite anchored elsewhere can never match"
        )
    return PathRewrite(
        from_prefix=from_prefix.rstrip("/"), to_prefix=to_prefix.rstrip("/")
    )


def _parse_schema(raw: dict[str, Any]) -> SchemaSource:
    source = str(raw.get("source", "file"))
    location = str(raw.get("path") or raw.get("url") or "")
    refresh = int(raw.get("refresh_seconds", _DEFAULT_REFRESH_SECONDS))
    return SchemaSource(source=source, location=location, refresh_seconds=refresh)
