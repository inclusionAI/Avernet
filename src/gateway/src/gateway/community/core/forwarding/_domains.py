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

import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

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


#: One DNS label: alphanumeric at both ends, hyphens allowed only inside.
#: Underscores are tolerated throughout because internal service names use them.
#: Validating per label rather than across the whole name is what rejects an
#: empty label (``foo..bar``) and a hyphen-only one (``-``) — both structurally
#: impossible names that a single whole-string character class would admit.
_LABEL = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_-]*[A-Za-z0-9_])?")

#: RFC 1035 §2.3.4. A longer label cannot be encoded, let alone resolved.
_MAX_LABEL_LENGTH = 63


def _names_a_host(host: str) -> bool:
    """Whether *host* is something a client could resolve and dial.

    Structure only. Whether the name *does* resolve is not knowable at boot —
    an upstream may legitimately not be provisioned yet — so this rejects names
    that could never resolve, not names that merely happen not to.
    """
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return True
    # A single trailing dot is a fully-qualified name rooted at the DNS root
    # (``up.example.``), which is legal; anything more is an empty label.
    labels = host[:-1].split(".") if host.endswith(".") else host.split(".")
    return bool(labels) and all(
        len(label) <= _MAX_LABEL_LENGTH and _LABEL.fullmatch(label) is not None
        for label in labels
    )


def _expand_vars(value: str, variables: dict[str, str]) -> str:
    """Expand ``${VAR}`` references from *variables* (missing key → empty)."""
    return _ENV_REF.sub(lambda m: variables.get(m.group(1), ""), value)


@dataclass(frozen=True)
class Server:
    """A resolved upstream target, addressable on either plane.

    ``base_url`` must carry a scheme — ``http``, ``https``, ``ws`` or ``wss`` —
    **and** name a host, and both are validated here rather than at the point of
    use, so a bad value fails the boot with the server named instead of
    producing a broken request URL at the first call. (A scheme-less value is
    not merely untidy: the forwarder concatenates it with the path, and
    ``backend.example/openapi/v1`` parses as a *relative* URL with an empty
    host.)

    A scheme alone is not enough to make a URL dialable, which is why the host
    is checked separately: ``https:///engine-proxy`` has a scheme and a
    non-empty remainder, yet its authority is empty, so the derived
    ``wss:///engine-proxy`` names no host and every dial fails at runtime —
    exactly the failure this validation exists to move to the boot.

    Which of the four schemes is written does not decide which planes the
    upstream can serve; the domain does that. The scheme only says TLS or not,
    and :attr:`http_base_url` / :attr:`websocket_base_url` spell that for
    whichever plane is asking.
    """

    name: str
    base_url: str

    def __post_init__(self) -> None:
        scheme, separator, _ = self.base_url.partition("://")
        if not separator or scheme.lower() not in _SCHEME_IS_SECURE:
            raise ValueError(
                f"upstream server {self.name!r}: base_url {self.base_url!r} must "
                f"carry a scheme (one of {sorted(_SCHEME_IS_SECURE)}) — a value "
                f"without one is a relative URL with no host"
            )
        self._validate_authority()

    def _validate_authority(self) -> None:
        """Refuse an authority that names no reachable host, or a broken port."""
        split = urlsplit(self.base_url)
        try:
            split.port
        except ValueError:
            raise ValueError(
                f"upstream server {self.name!r}: base_url {self.base_url!r} has "
                f"an unusable port — it must be a number"
            ) from None
        # ``hostname`` is None for an empty authority and unwraps an IPv6
        # literal's brackets, so this covers both spellings.
        if not _names_a_host(split.hostname or ""):
            raise ValueError(
                f"upstream server {self.name!r}: base_url {self.base_url!r} must "
                f"name a host — a scheme on its own leaves nothing to dial, so "
                f"every request for this server would fail at call time"
            )
        if split.query or split.fragment:
            # The request path is appended to this value, so a query or fragment
            # here swallows it: `https://up.example/base?fixed=1` becomes
            # `…/base?fixed=1/proxypass/…`, putting the whole upstream path
            # inside the query string.
            raise ValueError(
                f"upstream server {self.name!r}: base_url {self.base_url!r} must "
                f"be an origin and optional base path only — the request path is "
                f"appended to it, so a query or fragment would swallow that path"
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

        The prefix matches on **segment boundaries**, not characters: either the
        path is the prefix exactly, or the prefix is followed by ``/``. A
        character-wise ``startswith`` would rewrite ``/a/v20/ws`` under a prefix
        of ``/a/v2`` and produce ``/proxypass0/ws`` — a path on the upstream that
        nobody asked for and that no reader would predict.

        A path that does not carry the prefix is returned unchanged. Validation
        makes that unreachable for a path the domain map resolved here, so it is
        a defensive branch rather than a supported one.
        """
        if path == self.from_prefix:
            return self.to_prefix
        if path.startswith(f"{self.from_prefix}/"):
            return f"{self.to_prefix}{path[len(self.from_prefix) :]}"
        return path


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


#: Every key each block of the ``upstreams`` section understands. An unknown one
#: is refused rather than ignored: a key that is silently dropped falls back to
#: a default, and for ``protocols`` that default decides *which plane* a domain
#: exposes. ``protcols: [websocket]`` would otherwise turn the socket domain
#: into an HTTP route — on the one prefix the shipped table exempts from
#: authentication.
_DOMAIN_KEYS = frozenset({"server", "schema", "protocols", "rewrite"})
_SERVER_KEYS = frozenset({"base_url"})
_REWRITE_KEYS = frozenset({"from", "to"})
_SCHEMA_KEYS = frozenset({"source", "path", "url", "refresh_seconds"})


def _reject_unknown_keys(
    what: str, spec: dict[str, Any], allowed: frozenset[str]
) -> None:
    unknown = sorted(str(key) for key in spec if str(key) not in allowed)
    if unknown:
        raise ValueError(
            f"{what}: unknown key(s) {unknown} (expected any of {sorted(allowed)}) "
            f"— refused rather than ignored, because a dropped key silently "
            f"takes the default instead of what was written"
        )


def _parse_servers(raw: dict[str, Any], variables: dict[str, str]) -> dict[str, Server]:
    servers: dict[str, Server] = {}
    for name, raw_spec in raw.items():
        spec = cast("dict[str, Any]", raw_spec or {})
        _reject_unknown_keys(f"upstream server {name!r}", spec, _SERVER_KEYS)
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
    for name, raw_spec in raw.items():
        spec = cast("dict[str, Any]", raw_spec or {})
        _reject_unknown_keys(f"domain {name!r}", spec, _DOMAIN_KEYS)
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
            schema=_parse_schema(name, spec.get("schema") or {}),
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

    ``from`` must begin at the domain's own prefix, **on a segment boundary**. A
    rewrite anchored anywhere else could never fire — the domain map matches the
    leading segment exactly, so nothing routed here can carry another prefix —
    and it is a configuration mistake worth refusing at startup rather than a
    rule that silently never matches.

    The boundary is the whole point of the check. A textual ``startswith`` would
    accept ``/openapi/v1/engine-v2`` for the ``engine`` domain, since it does
    begin with those characters; but ``/openapi/v1/engine-v2/…`` resolves to a
    domain *named* ``engine-v2``, never to this one, so the rule would be
    accepted and then never apply — the exact silent no-op being guarded
    against.
    """
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise ValueError(
            f"domain {name!r}: rewrite must be a mapping with 'from' and 'to'"
        )
    spec = cast(dict[str, Any], raw)
    _reject_unknown_keys(f"domain {name!r}: rewrite", spec, _REWRITE_KEYS)
    from_prefix = str(spec.get("from", ""))
    to_prefix = str(spec.get("to", ""))
    if not from_prefix or not to_prefix:
        raise ValueError(f"domain {name!r}: rewrite needs both 'from' and 'to'")
    if "?" in from_prefix or "#" in from_prefix:
        # A request path never carries either, so such a rule could not match —
        # the same silent no-op the anchor check below exists to prevent. The
        # anchor check alone does not catch it: `/openapi/v1/engine/v2?x` does
        # begin with the domain prefix and a `/`, so it passes that test.
        raise ValueError(
            f"domain {name!r}: rewrite.from {from_prefix!r} must be a path "
            f"prefix with no query or fragment — a request path contains "
            f"neither, so this rule could never match"
        )
    if not to_prefix.startswith("/"):
        # The relay concatenates this straight onto the upstream origin, so a
        # relative value silently becomes part of the *host*: `to: proxypass`
        # against `wss://proxy.internal` yields `wss://proxy.internalproxypass/…`,
        # which is a different host or an unparseable URI, not a path.
        raise ValueError(
            f"domain {name!r}: rewrite.to {to_prefix!r} must be an absolute path "
            f"starting with '/' — it is joined to the upstream origin, so a "
            f"relative value would be absorbed into the host"
        )
    if "?" in to_prefix or "#" in to_prefix:
        # Being absolute is not enough: the request tail is appended to this
        # prefix, so `to: /proxypass?fixed=1` sends the upstream a path of just
        # `/proxypass` and folds the tail — credential included — into the query.
        raise ValueError(
            f"domain {name!r}: rewrite.to {to_prefix!r} must be a path prefix "
            f"with no query or fragment — the rest of the request path is "
            f"appended to it, and would land inside the query instead"
        )
    anchored = from_prefix.rstrip("/")
    if anchored != domain_prefix and not anchored.startswith(f"{domain_prefix}/"):
        raise ValueError(
            f"domain {name!r}: rewrite.from {from_prefix!r} must be "
            f"{domain_prefix!r} or a path beneath it — a rewrite anchored "
            f"elsewhere can never match"
        )
    return PathRewrite(
        from_prefix=from_prefix.rstrip("/"), to_prefix=to_prefix.rstrip("/")
    )


def _parse_schema(name: str, raw: dict[str, Any]) -> SchemaSource:
    _reject_unknown_keys(f"domain {name!r}: schema", raw, _SCHEMA_KEYS)
    source = str(raw.get("source", "file"))
    location = str(raw.get("path") or raw.get("url") or "")
    refresh = int(raw.get("refresh_seconds", _DEFAULT_REFRESH_SECONDS))
    return SchemaSource(source=source, location=location, refresh_seconds=refresh)
