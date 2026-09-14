"""The credential endpoint guard — the SSRF rule applied before a write.

An object-store credential carries an endpoint, and the platform will connect
to whatever that endpoint names on the next apply. :func:`endpoint_refusal` is
the rule the credential surface runs *before persisting* one, which is the
only point at which refusing costs nobody an apply.

Two checks, in this order, because the cheap one is host-independent:

1. **URL shape** — scheme, host, userinfo — refused before any DNS.
2. **Resolution and address validation** — every address the name resolves to
   must be globally routable; loopback, link-local, ULA, private, multicast
   and reserved are all refused. Every address is validated, not a lucky
   first one.

The deployment transport allowlist
(``user_config.bot_config_manifest.fetch_transport_allowlist``, parsed by
:func:`~agentclaw.community.core.bot_config_manifest.fetch.limits.transport_allowlist_from_config`
and handed to ``SourceCredentialService`` by the composition root) exempts
an exact host from the public-only and https-only rules and from nothing
else: an internal object store is a destination the deployment declared, so
its endpoint stays registrable. Matching is exact-host — the DNS-rebinding
lesson: a hostname on the list is the hostname exempted, with no pattern
semantics to reason about.

Precedent: the engine repo's ``resource_materialization.py`` guarded
downloader — same layering (shape → global-only resolution), re-implemented
backend-side behind its own matrix.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Iterable, Optional

import httpx

from agentclaw.community.core.bot_config_manifest.fetch.errors import (
    FetchRefusedError,
)
from agentclaw.community.core.bot_config_manifest.fetch.limits import SAFE_SCHEMES
from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    Resolver as ResolverType,
)
from agentclaw.community.log import get_logger

logger = get_logger()


#: Default resolution is real DNS; tests inject deterministic answers.
def _resolve_via_socket(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise FetchRefusedError(f"cannot resolve host: {host!r}") from exc
    return sorted({info[4][0] for info in infos})


def endpoint_refusal(
    endpoint: str,
    *,
    allow_hosts: Iterable[str] = (),
    resolver: Optional[ResolverType] = None,
) -> Optional[str]:
    """Why this object-store endpoint may not be stored, or ``None``.

    Two rules — URL shape, then every resolved address — applied by the
    credential surface **before persisting** an endpoint, which is the only
    point at which refusing costs nobody an apply.

    It exists because of a wrong assumption: that an object-store endpoint
    needs no SSRF machinery, on the argument that it comes from a credential
    rather than from a tenant's document — but a credential is
    itself written by an authenticated tenant application through the API, so
    "not from the document" is not "not from the tenant". Without this,
    ``http://169.254.169.254/`` is a storable endpoint and the platform will
    connect to it on the next apply.

    **A host that will not resolve is not refused**, and that concession is
    deliberate. At fetch time a resolution failure is a statement of fact:
    the read cannot happen, so refusing it describes reality. Here it
    would be a prediction — the pod that stores a credential is not the pod
    that later reads with it, and split-horizon DNS, a private zone, or a
    minute's outage are all ordinary reasons a good endpoint does not answer
    *here, now*. Refusing on that would make credential writes depend on the
    network while buying nothing against the case this guard is for: whoever
    controls a name can answer with a public address at write time and a
    link-local one at read time, so the strict form was TOCTOU regardless. It
    is logged, because in practice an endpoint that does not resolve is a
    typo, and a typo is worth a line in the log and not a refusal.

    What survives that concession is the part that does the work. A literal
    address needs no DNS at all — ``getaddrinfo`` answers a numeric host from
    the string — so ``https://169.254.169.254/`` is still refused, as is any
    name that *does* resolve somewhere private, and every shape rule above it.

    ``resolver`` defaults to real DNS; tests inject. A host on the deployment
    transport allowlist is exempt from the public-only rule — the deployment
    declared that destination.
    """
    if any(ch in endpoint for ch in "\r\n\x00"):
        return "endpoint must not contain control characters"
    try:
        parsed = httpx.URL(endpoint)
    except (httpx.InvalidURL, ValueError, UnicodeError):
        return "endpoint is not a valid URL"
    host = parsed.host
    if not host:
        return "endpoint must be an absolute URL with a host"
    if parsed.userinfo:
        return "endpoint must not carry userinfo — the key pair is the credential"
    hosts = frozenset(allow_hosts)
    if parsed.scheme not in SAFE_SCHEMES and not (
        parsed.scheme == "http" and host in hosts
    ):
        return f"endpoint scheme {parsed.scheme!r} is not allowed"
    if host in hosts:
        return None
    try:
        resolved = (resolver or _resolve_via_socket)(host)
    except (FetchRefusedError, OSError):
        resolved = []
    # Parsed one at a time, not as a comprehension that raises on the first
    # bad entry: a resolver answering ["10.0.0.1", "garbage"] would otherwise
    # discard the whole list — including the private address that is the
    # reason to refuse — and fall through to the permissive branch below.
    addresses = []
    for ip in resolved:
        try:
            addresses.append(ipaddress.ip_address(ip))
        except ValueError:
            continue
    if not addresses:
        # Unresolvable, or answered with something that is not an address:
        # noted, not refused. See the docstring — this is the one rule that
        # would have made storing a credential depend on this pod's DNS.
        logger.warning(
            "object store endpoint host does not resolve here: %r. Storing it "
            "anyway; check it for a typo, because a fetch through it will "
            "fail.",
            host,
        )
        return None
    if any(_refused_address(ip) for ip in addresses):
        # Every address, not a lucky first one.
        return f"endpoint host resolves to a non-public address: {host!r}"
    return None


def _refused_address(ip: ipaddress._BaseAddress) -> bool:
    """The refusal set, explicit.

    ``is_global`` alone is insufficient: Python counts globally-scoped
    multicast (224.0.0.1) as global, while the issue's rule refuses
    multicast outright. Loopback/link-local/ULA/private/reserved/
    unspecified all answer ``is_global`` False already — they stay in the
    boolean so the refusal set reads as one rule rather than one fact.
    """
    return (
        not ip.is_global
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_link_local
        or ip.is_loopback
    )
