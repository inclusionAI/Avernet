"""Catch-all forwarding entrypoint — the runtime request path.

One route handles every request that is not a gateway-local endpoint:

1. resolve the domain claiming this path *on the HTTP plane* (none → 404; the
   gateway forwards only into known domains, never as an open proxy);
2. authenticate (fail-closed) — a known domain still requires a principal;
3. forward the path **verbatim** to the resolved server and stream the response
   back unchanged.

The upstream (the backend) produces the response envelope; the gateway relays it
verbatim and only synthesises an envelope for its *own* errors (unknown domain,
auth failure, upstream unavailable). The auth workstream attaches the signed
principal at the forwarder seam.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import replace

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.requests import HTTPConnection
from starlette.responses import Response

from gateway.community.adapters.web._log_redaction import redact_credentials
from gateway.community.logger import get_logger
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import (
    AccessKeyPrincipal,
    AppPrincipal,
    BotPrincipal,
    CredentialBundle,
    Principal,
    PrincipalType,
    UserPrincipal,
)
from gateway.community.spi.forwarder import ForwardRequest
from gateway.community.spi.principal_signer import PrincipalSigner
from gateway.community.tracer import get_tracer_plugin

logger = get_logger("forward")

_ALL_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]


def _request_id() -> str:
    try:
        return str(get_tracer_plugin().get_trace_id())
    except Exception:
        return ""


def _error(status: int, subcode: int, message: str) -> JSONResponse:
    """A gateway-generated error in the standard envelope shape."""
    return JSONResponse(
        status_code=status,
        content={
            "code": status * 1000 + subcode,
            "message": message,
            "data": None,
            "request_id": _request_id(),
        },
    )


def _bundle(connection: HTTPConnection) -> CredentialBundle:
    """Credentials as they arrived, for any connection kind.

    Typed on ``HTTPConnection`` rather than ``Request`` so the engine socket's
    handshake builds its bundle exactly the way a forwarded request does — a
    WebSocket carries the same headers, cookies and query a request does, and
    the two must not drift into reading credentials differently.
    """
    return CredentialBundle(
        headers={k.lower(): v for k, v in connection.headers.items()},
        cookies=dict(connection.cookies),
        query=dict(connection.query_params),
    )


def _target_url(base_url: str, path: str, request: Request) -> str:
    """The upstream URL for *path* — the domain's, already rewritten if it
    declares one; otherwise the caller's path verbatim."""
    base = base_url.rstrip("/")
    query = request.url.query
    return f"{base}{path}" + (f"?{query}" if query else "")


_ABSENT = "-"


def _identity_label(identities: dict[PrincipalType, Principal]) -> tuple[str, str]:
    """``(tenant, caller)`` for the log line, from the resolved identity set.

    The gateway is the only hop that sees the credentials, so it is the only
    place that can say *who* a request turned out to be; the upstream can only
    report what it was told. Naming the identity set on both sides of the seam
    is what makes a mismatch — a request scoped to a tenant the gateway did not
    resolve — visible as a difference between two log lines instead of an
    invisible one.

    ``caller`` names each identity by the field that identifies it, so a
    multi-identity route (user + app) is legible as such. No credential is
    formatted: ``Bot.token`` and ``AccessKey.access_key_token`` are secrets the
    gateway forwards, never ones it prints.
    """
    if not identities:
        return _ABSENT, _ABSENT
    labels = []
    tenants = set()
    for principal in identities.values():
        if not isinstance(principal, UserPrincipal) and principal.tenant:
            tenants.add(principal.tenant)
        if isinstance(principal, UserPrincipal):
            labels.append(f"user:{principal.subject.id}")
        elif isinstance(principal, AppPrincipal):
            labels.append(f"app:{principal.app.app_id}")
        elif isinstance(principal, BotPrincipal):
            labels.append(f"bot:{principal.bot.bot_uuid}")
        elif isinstance(principal, AccessKeyPrincipal):
            labels.append(f"access_key:{principal.access_key.access_key}")
        else:
            labels.append(f"{principal.type}:{_ABSENT}")
    # A set that disagrees is logged as the several tenants it names rather than
    # as one of them: downstream verification refuses such a token outright, and
    # a log line that quietly picked a winner would hide exactly that failure.
    tenant = "+".join(sorted(tenants)) if tenants else _ABSENT
    return tenant, "+".join(labels)


_PRINCIPAL_HEADER = "X-Avernet-Principal"
# Inbound headers that must NEVER pass through to the upstream (call site
# strips these when building ForwardRequest.headers). `host` is dropped so
# httpx sets it from the upstream URL; `X-Avernet-Principal` is dropped so a
# caller cannot forge the identity header (the gateway injects its own signed
# token).
_INBOUND_STRIP = frozenset({"host", "x-avernet-principal"})


async def _attach_identities(
    forward: ForwardRequest,
    identities: dict[PrincipalType, Principal],
    *,
    signer: PrincipalSigner,
    audience: str,
) -> ForwardRequest:
    """Inject the signed identity set into the forwarded request (auth §7.1).

    Components must verify this token — never trust a bare
    ``X-Avernet-Principal`` header. An empty identity set adds no header.
    """
    if not identities:
        return forward
    token = await signer.sign(identities, audience=audience)
    headers = {**forward.headers, _PRINCIPAL_HEADER: token}
    return replace(forward, headers=headers)


async def forward_request(request: Request) -> Response:
    """Resolve domain → authenticate → forward verbatim, streaming the response."""
    started = time.perf_counter()
    path = request.url.path
    # Asked for the *HTTP* domain, not for whichever domain claims the path.
    # A socket domain is not a candidate here at all, so it can neither become an
    # HTTP proxy into its upstream nor shadow a broader HTTP domain that also
    # claims the path — an HTTP request beneath a socket-only prefix still
    # resolves to the domain that serves the prefix above it.
    domain = request.app.state.domain_map.http_domain_for(path)
    if domain is None:
        logger.info("no route for %s %s", request.method, path)
        return _error(404, 1, "no route for path")
    server = domain.server
    upstream_path = domain.upstream_path(path)

    try:
        identities = await request.app.state.authenticator.authenticate(
            request.method, path, _bundle(request)
        )
    except AuthError as exc:
        logger.warning("auth failed for %s %s: %s", request.method, path, exc)
        return _error(401, 1, str(exc))

    body = await request.body()
    try:
        forward = await _attach_identities(
            ForwardRequest(
                method=request.method,
                url=_target_url(server.http_base_url, upstream_path, request),
                # Drop Host (httpx sets it from the upstream URL) and any
                # caller-supplied X-Avernet-Principal (forgery guard).
                headers={
                    k: v
                    for k, v in request.headers.items()
                    if k.lower() not in _INBOUND_STRIP
                },
                content=body,
            ),
            identities,
            signer=request.app.state.principal_signer,
            audience=server.name,
        )
    except Exception:
        logger.exception("principal signing failed")
        return _error(500, 1, "principal signing failed")

    cm = request.app.state.forwarder.forward(forward)
    try:
        upstream = await cm.__aenter__()
    except Exception:
        logger.warning(
            "upstream unavailable for %s %s", request.method, path, exc_info=True
        )
        return _error(502, 1, "upstream unavailable")

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.body:
                yield chunk
        finally:
            await cm.__aexit__(None, None, None)

    response = StreamingResponse(stream(), status_code=upstream.status_code)
    tenant, caller = _identity_label(identities)
    # The identity fields are the reason this line exists at all: an upstream can
    # report the tenant it was *told*, only the gateway can report the tenant it
    # *resolved*. ``duration_ms`` measures up to the upstream's response head —
    # the body is still streaming when this is written, so it is time-to-first-
    # byte, not the full transfer.
    #
    # The upstream URL carries the caller's query verbatim, and on this plane
    # that query can hold a credential — the socket relay's is the documented
    # case, but nothing stops an HTTP caller passing one the same way. The
    # filter installed for uvicorn's own request lines does not reach this
    # logger, so the value is redacted here, at the format site.
    logger.info(
        "forward %s %s -> %s status=%d duration_ms=%.1f tenant=%s caller=%s "
        "domain=%s server=%s request_id=%s",
        request.method,
        path,
        redact_credentials(forward.url),
        upstream.status_code,
        (time.perf_counter() - started) * 1000,
        tenant,
        caller,
        domain.name,
        server.name,
        _request_id() or _ABSENT,
    )
    # Set raw headers directly so duplicate headers (Set-Cookie) survive verbatim.
    response.raw_headers = [
        (k.encode("latin-1"), v.encode("latin-1")) for k, v in upstream.headers
    ]
    return response
