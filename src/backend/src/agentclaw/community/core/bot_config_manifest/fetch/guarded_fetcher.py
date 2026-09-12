"""Fetch-road failure vocabulary and the credential endpoint guard (W2, #1470).

Two things live here, and only one of them ever had a transport. The
``FetchRefusedError`` / ``FetchFailedError`` pair is the vocabulary both
remaining roads — the object store and git — classify their outcomes with,
and ``FetchedObject`` is the receipt-bearing record the content store takes.
``endpoint_refusal`` is the SSRF rule the credential surface runs before it
will persist an object-store endpoint.

Defense order, where it still applies:

1. **URL shape** (scheme, host, userinfo) — cheap, host-independent
   refusals happen before DNS.
2. **Resolution and address validation** — every address the name resolves
   to must be globally routable (loopback/link-local/ULA/multicast/reserved
   are all refused).

Precedent: the engine repo's ``resource_materialization.py`` guarded
downloader — same layering (shape → global-only resolution), re-implemented
backend-side behind its own matrix.
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

import httpx

from agentclaw.community.core.bot_config_manifest.fetch.limits import SAFE_SCHEMES
from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    Resolver as ResolverType,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class FetchRefusedError(Exception):
    """The request never left: transport policy refused it.

    A configuration-class answer (refused scheme/address/policy/budget),
    and no internal text rides out — the fetching side reports the rule,
    not the site's or the caller's data.
    """


class FetchFailedError(Exception):
    """The request was attempted and the source failed it.

    Non-2xx terminal statuses, transport failures, and a digest mismatch —
    the last is deliberately this, not a "success with corrupted bytes".
    """


#: Default resolution is real DNS; tests inject deterministic answers.
def _resolve_via_socket(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise FetchRefusedError(f"cannot resolve host: {host!r}") from exc
    return sorted({info[4][0] for info in infos})


@dataclass(frozen=True)
class FetchedObject:
    """Fetched bytes with their receipt — write-or-hash material, never run."""

    bytes: bytes
    sha256: str
    url: str
    content_type: Optional[str]
    fetched_at: datetime
    size_bytes: int


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

    It exists because of a wrong assumption. The object-store road drops the
    fetcher's SSRF machinery on the argument that its endpoint comes from a
    credential rather than from a tenant's document — but a credential is
    itself written by an authenticated tenant application through the API, so
    "not from the document" is not "not from the tenant". Without this,
    ``http://169.254.169.254/`` is a storable endpoint and the platform will
    connect to it on the next apply.

    **A host that will not resolve is not refused**, and that divergence from
    the fetch road is deliberate. There, resolution failure is a statement of
    fact: the hop cannot happen, so refusing it describes reality. Here it
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
    transport allowlist is exempt from the public-only rule, exactly as it is
    on the fetch road — the deployment declared that destination.
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
        # Every address, not a lucky first one — the same rule the fetch road
        # applies, for the same reason.
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
