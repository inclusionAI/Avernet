"""The guarded fetcher — platform-side content download with SSRF defense (W2, #1470).

Manifest sources are caller-supplied URLs the platform fetches on the
caller's behalf, which is the canonical SSRF surface: the fetcher here is
the single funnel every manifest byte passes through, and its rules are
the issue's acceptance list made code.

Defense order, and why each step is where it is:

1. **URL shape** (scheme, host, userinfo) — cheap, host-independent
   refusals happen before DNS.
2. **Resolution and address validation** — every address the name resolves
   to must be globally routable (loopback/link-local/ULA/multicast/reserved
   are all refused), and the connection pins the *lowest validated
   address*: a hostname that re-resolves between the check and the connect
   cannot reach a refused target, because the name is never resoled again
   on the wire. The original identity rides along as the Host header and
   the TLS SNI (httpx ``extensions['sni_hostname']``).
3. **Redirects stay in the funnel** — no framework auto-following: each
   hop is re-validated through exactly this path (shape, addresses,
   authorization policy) under a hard hop budget.
4. **Limits enforced while streaming** — the per-entry byte cap counts
   bytes as they arrive, never trusting a declared ``Content-Length``.
5. **The sha256 is computed on the same pass**, and a declared digest that
   does not match is a *fetch failure*: the fetcher never returns bytes it
   was told to pin and did not ("损坏的成功" is the acceptance text's
   refusal).

Credential presentation is declared here (``CredentialInjector``) and
bound by W3: the transport asks it for headers and consults the
``AuthorizationPolicy`` on every hop, because "this credential may leave
its prefixes" is a credentials decision, not a transport one.

Precedent: the engine repo's ``resource_materialization.py`` guarded
downloader — same layering (shape → global-only resolution → pinned
connection), re-implemented backend-side behind its own matrix.
"""
from __future__ import annotations

import hashlib
import ipaddress
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional, Protocol

import httpx

from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    DIGEST_RE,
    FetchBudget,
    MAX_REDIRECTS,
    SAFE_SCHEMES,
)
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


class CredentialInjector(Protocol):
    """Headers to present when fetching ``url``. W3 binds this."""

    def headers_for(self, url: httpx.URL) -> dict[str, str]: ...


class AuthorizationPolicy(Protocol):
    """Per-hop authorization; raising refuses the hop. W3 binds this.

    Consulted with the about-to-be-fetched URL — including every redirect
    target — so a credential cannot be walked out of its prefixes.
    """

    def reauthorize(self, url: httpx.URL) -> None: ...


#: Default resolution is real DNS; tests inject deterministic answers.
def _resolve_via_socket(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise FetchRefusedError(f"cannot resolve host: {host!r}") from exc
    return sorted({info[4][0] for info in infos})


@dataclass(frozen=True)
class FetchRequest:
    """One entry's fetch as the (future W4) orchestrator issues it."""

    url: str
    expected_digest: Optional[str] = None
    category: str = "resources_file"
    injector: Optional[CredentialInjector] = None
    policy: Optional[AuthorizationPolicy] = None


@dataclass(frozen=True)
class FetchedObject:
    """Fetched bytes with their receipt — write-or-hash material, never run."""

    bytes: bytes
    sha256: str
    url: str
    content_type: Optional[str]
    fetched_at: datetime
    size_bytes: int


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


def _host_header(url: httpx.URL) -> str:
    """The Host the origin expects: host only on the scheme's default port.

    httpcore composes exactly this when no Host is forced; forcing it is
    necessary here because the dialed address is the pinned IP, so the
    header must be reconstructed — with the non-default port, or
    port-signing origins (the OSS share-link family) reject the request.
    """
    default_port = 443 if url.scheme == "https" else 80
    if url.port is None or url.port == default_port:
        return url.host
    return f"{url.host}:{url.port}"


class GuardedFetcher:
    """The one funnel.

    ``resolver`` and ``_transport`` are the two test seams: production uses
    real DNS and no transport override. ``transport_allowlist`` is the
    deployment decision (config, not environment): the composition root
    reads ``user_config.bot_config_manifest.fetch_transport_allowlist``
    from ``application.yaml`` — via
    :func:`~agentclaw.community.core.bot_config_manifest.fetch.limits.transport_allowlist_from_config`
    — and passes it here at construction. A **fresh ``httpx.Client`` per hop** —
    no fetcher-lifetime client, deliberately: httpcore keys its connection
    pool on the *dialed* origin, which under IP pinning is the validated
    address, so a shared client would hand a later request (another
    hostname, its Host header, W3's credential headers) an already-open TLS
    connection *certificated for a different name*. One client per hop caps
    reuse at zero; the engine precedent does the same per request.
    """

    def __init__(
        self,
        resolver: ResolverType = _resolve_via_socket,
        _transport: httpx.BaseTransport | None = None,
        transport_allowlist: Iterable[str] = (),
    ) -> None:
        self._resolver = resolver
        self._transport = _transport
        # frozenset ≡ exact-host matching: no pattern semantics to reason
        # about, and a re-issued construction cannot mutate the exemption.
        self._allow_hosts = frozenset(transport_allowlist)

    def _validate_url(self, url: str) -> httpx.URL:
        """Step 1: shape — refused before any DNS or wire contact."""
        try:
            parsed = httpx.URL(url)
        except (httpx.InvalidURL, ValueError) as exc:
            raise FetchRefusedError("untrusted URL shape") from exc
        host = parsed.host
        if not host:
            raise FetchRefusedError("untrusted URL: no host")
        if parsed.userinfo:
            # Credentials in the URL would persist in logs and audits; the
            # manifest carries a credential *name*, never a value.
            raise FetchRefusedError("untrusted URL: userinfo present")
        if parsed.scheme not in SAFE_SCHEMES and not (
            parsed.scheme == "http" and host in self._allow_hosts
        ):
            raise FetchRefusedError(f"untrusted URL scheme: {parsed.scheme!r}")
        return parsed

    def _pinned_address(self, host: str) -> ipaddress._BaseAddress:
        """Step 2: validate every resolved address, pin the lowest one.

        The connection goes to a *validated* address — the name is never
        consulted again on the wire, so a re-resolving hostname cannot swing
        the connection to a refused target between check and connect. A host
        on the deployment transport allowlist is exempt from the public-only
        rule (the deployment declared the destination), never from any other.
        """
        exempt = host in self._allow_hosts
        try:
            resolved = self._resolver(host)
        except FetchRefusedError:
            raise
        except OSError as exc:
            raise FetchRefusedError(f"cannot resolve host: {host!r}") from exc
        if not resolved:
            raise FetchRefusedError(f"cannot resolve host: {host!r}")
        try:
            addresses = [ipaddress.ip_address(ip) for ip in resolved]
        except ValueError as exc:
            raise FetchRefusedError(f"unresolved host: {host!r}") from exc
        if not exempt and any(_refused_address(ip) for ip in addresses):
            # Every address validates, not a lucky first one.
            raise FetchRefusedError(f"non-public address for host: {host!r}")
        return min(addresses, key=lambda ip: (ip.version, int(ip)))

    def fetch(self, request: FetchRequest) -> FetchedObject:
        if request.expected_digest is not None and not DIGEST_RE.match(
            request.expected_digest
        ):
            raise FetchRefusedError(
                f"untrusted declared digest: {request.expected_digest!r}"
            )
        current = self._validate_url(request.url)
        if request.policy is not None:
            request.policy.reauthorize(current)
        hops = MAX_REDIRECTS
        while True:
            pinned = self._pinned_address(current.host)
            pinned_url = current.copy_with(host=str(pinned))
            headers: dict[str, str] = {
                # The original name is the identity on the wire — Host
                # header and SNI — while the connection itself goes to the
                # pinned address (the extension preserves the TLS name).
                "host": _host_header(current),
            }
            if request.injector is not None:
                headers.update(request.injector.headers_for(current))
            # Streaming from the first byte: the entry cap has to count
            # bytes as they arrive, which ``.get()`` would defeat by
            # buffering the whole body first.
            try:
                with httpx.Client(
                    transport=self._transport, follow_redirects=False
                ) as client, client.stream(
                    "GET",
                    str(pinned_url),
                    headers=headers,
                    extensions={"sni_hostname": current.host},
                    timeout=FetchBudget(category=request.category).timeout_s,
                ) as response:
                    if 300 <= response.status_code < 400:
                        location = response.headers.get("location", "")
                        if not location:
                            raise FetchFailedError(
                                f"redirect without location: {response.status_code}"
                            )
                        hops -= 1
                        if hops < 0:
                            raise FetchRefusedError("too many redirects")
                        # The next hop re-resolves, re-validates and — via a
                        # fresh client above — always re-handshakes: a
                        # redirected request never rides the first hop's
                        # TLS connection.
                        current = self._validate_url(str(current.join(location)))
                        if request.policy is not None:
                            request.policy.reauthorize(current)
                        continue
                    if response.status_code >= 400:
                        raise FetchFailedError(
                            f"source answered {response.status_code}"
                        )
                    return self._collect(response, current, request)
            except httpx.TransportError as exc:
                # Connect/read/protocol/TLS failures carry caller-irrelevant
                # detail; the contract says "attempted and failed", and W4's
                # entry classification depends on that type.
                raise FetchFailedError("source transport failed") from exc

    def _collect(
        self, response: httpx.Response, url: httpx.URL, request: FetchRequest
    ) -> FetchedObject:
        """Stream under the byte cap; hash on the same pass; verify the receipt."""
        limit = FetchBudget(category=request.category).entry_limit
        hasher = hashlib.sha256()
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > limit:
                raise FetchRefusedError(
                    f"response exceeds the {limit}-byte entry cap"
                )
            hasher.update(chunk)
            chunks.append(chunk)
        body = b"".join(chunks)
        digest = "sha256:" + hasher.hexdigest()
        if request.expected_digest is not None and digest != request.expected_digest:
            raise FetchFailedError("digest mismatch")
        # Host only, never the URL: query strings are where signed-source
        # tokens live, and logs have the wider audience.
        logger.info(
            "[manifest.fetch] fetched host=%s bytes=%s digest=%s",
            url.host,
            total,
            digest,
        )
        return FetchedObject(
            bytes=body,
            sha256=digest,
            url=str(url),
            content_type=response.headers.get("content-type"),
            fetched_at=datetime.now(timezone.utc),
            size_bytes=total,
        )
