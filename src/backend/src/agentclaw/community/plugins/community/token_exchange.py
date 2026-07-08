"""PassthroughTokenExchangePlugin — community token exchange.

The corp ``TokenExchangePlugin`` trades a Buservice IAM subject token for a
downstream BUC access token. A community deployment already holds a valid OIDC
access token after login, and the only downstream consumers are corp-specific —
so there is nothing to exchange. This impl returns the caller's *own* inbound
token (bearer header, else the OIDC cookie), making no internal call. The
endpoint stays mounted for frontend contract compatibility; the community
gateway runs ``auth.mode=none`` and does not validate the value.

A real, deployable implementation (not a ``MockSeam`` test double).
"""
from __future__ import annotations

from typing import Any

from fastapi import Request

from agentclaw.community.plugin_api.token_exchange import TokenExchangePlugin

_BEARER_COOKIE = "access_token"


class PassthroughTokenExchangePlugin(TokenExchangePlugin):
    """Return the caller's own inbound token; no internal exchange."""

    async def exchange_from_request(self, request: Request) -> dict[str, Any]:
        auth = request.headers.get("authorization") or ""
        parts = auth.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return {"access_token": parts[1].strip()}
        cookie_token = request.cookies.get(_BEARER_COOKIE) or ""
        return {"access_token": cookie_token.strip()}
