"""Delivery-layer auth dependency.

``require_principal`` snapshots the FastAPI request into a framework-agnostic
``CredentialBundle`` and delegates to the ``Authenticator`` built by the
composition root (stored on ``app.state``), mapping auth failure to HTTP 401 in
the standard response envelope (``401001``, the same code the forwarding path
answers a refused credential with).
The adapter imports only ``spi`` — the runner, route table, and strategies live
behind the ``Authenticator`` it receives.
"""

from __future__ import annotations

from fastapi import Request

from gateway.community.adapters.web._envelope import GatewayError
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import CredentialBundle, Principal


def _bundle(request: Request) -> CredentialBundle:
    return CredentialBundle(
        headers={k.lower(): v for k, v in request.headers.items()},
        cookies=dict(request.cookies),
        query=dict(request.query_params),
    )


async def require_principal(request: Request) -> Principal:
    """FastAPI dependency: authenticate the request or raise 401."""
    authenticator = request.app.state.authenticator
    try:
        principal: Principal = await authenticator.authenticate(
            request.method, request.url.path, _bundle(request)
        )
    except AuthError as err:
        raise GatewayError(401, 1, str(err)) from err
    return principal
