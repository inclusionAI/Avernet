"""Catch-all forwarding entrypoint — the runtime request path.

One route handles every request that is not a gateway-local endpoint:

1. resolve the domain from the leading path segment (unknown → 404; the gateway
   forwards only into known domains, never as an open proxy);
2. authenticate (fail-closed) — a known domain still requires a principal;
3. forward the path **verbatim** to the resolved server and stream the response
   back unchanged.

The upstream (the backend) produces the response envelope; the gateway relays it
verbatim and only synthesises an envelope for its *own* errors (unknown domain,
auth failure, upstream unavailable). The auth workstream attaches the signed
principal at the forwarder seam.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.requests import HTTPConnection
from starlette.responses import Response

from gateway.community.logger import get_logger
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import CredentialBundle, Principal, PrincipalType
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
    path = request.url.path
    domain = request.app.state.domain_map.domain_for(path)
    # A domain that does not answer HTTP is as unknown here as one that is not
    # configured at all. Socket domains are served by the WebSocket entrypoint
    # and must not become an HTTP proxy into their upstream as a side effect of
    # sharing the domain map.
    if domain is None or not domain.serves_http:
        return _error(404, 1, "no route for path")
    server = domain.server
    upstream_path = domain.upstream_path(path)

    try:
        identities = await request.app.state.authenticator.authenticate(
            request.method, path, _bundle(request)
        )
    except AuthError as exc:
        return _error(401, 1, str(exc))

    body = await request.body()
    try:
        forward = await _attach_identities(
            ForwardRequest(
                method=request.method,
                url=_target_url(server.base_url, upstream_path, request),
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
        return _error(502, 1, "upstream unavailable")

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.body:
                yield chunk
        finally:
            await cm.__aexit__(None, None, None)

    response = StreamingResponse(stream(), status_code=upstream.status_code)
    # Set raw headers directly so duplicate headers (Set-Cookie) survive verbatim.
    response.raw_headers = [
        (k.encode("latin-1"), v.encode("latin-1")) for k, v in upstream.headers
    ]
    return response
