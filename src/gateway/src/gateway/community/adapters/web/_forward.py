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

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.responses import Response

from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import CredentialBundle, Principal, PrincipalType
from gateway.community.spi.forwarder import ForwardRequest
from gateway.community.tracer import get_tracer_plugin

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


def _bundle(request: Request) -> CredentialBundle:
    return CredentialBundle(
        headers={k.lower(): v for k, v in request.headers.items()},
        cookies=dict(request.cookies),
        query=dict(request.query_params),
    )


def _target_url(base_url: str, request: Request) -> str:
    base = base_url.rstrip("/")
    query = request.url.query
    return f"{base}{request.url.path}" + (f"?{query}" if query else "")


def _attach_identities(
    forward: ForwardRequest, identities: dict[PrincipalType, Principal]
) -> ForwardRequest:
    """Forwarder seam for the resolved identities.

    Per auth design §7.1, components must NEVER trust a bare Principal header;
    the signing workstream swaps this no-op for a signed-token injection.
    Until then, the resolved identities are available here but NOT forwarded.
    """
    _ = identities  # referenced so the value is provably available at the seam
    return forward


async def forward_request(request: Request) -> Response:
    """Resolve domain → authenticate → forward verbatim, streaming the response."""
    path = request.url.path
    server = request.app.state.domain_map.resolve(path)
    if server is None:
        return _error(404, 1, "no route for path")

    try:
        identities = await request.app.state.authenticator.authenticate(
            request.method, path, _bundle(request)
        )
    except AuthError as exc:
        return _error(401, 1, str(exc))

    body = await request.body()
    forward = _attach_identities(
        ForwardRequest(
            method=request.method,
            url=_target_url(server.base_url, request),
            # Drop Host so httpx sets it from the upstream URL, not the gateway's.
            headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
            content=body,
        ),
        identities,
    )

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
