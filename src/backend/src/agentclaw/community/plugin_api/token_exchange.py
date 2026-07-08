"""TokenExchangePlugin — exchange a frontend SSO token for a backend access token.

Talks to Buservice (or its mock) — a system boundary. Per Rule 14 each
runtime gets its own impl:

- ``plugins.local.token_exchange.LocalTokenExchangePlugin``
- ``plugins.prod.token_exchange.ProdTokenExchangePlugin``
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from fastapi import Request
from agentclaw.community.plugin_api.base import Plugin


@runtime_checkable
class TokenExchangePlugin(Plugin, Protocol):
    """Exchange the inbound request's identity for a backend access token."""

    async def exchange_from_request(self, request: Request) -> dict[str, Any]:
        """Resolve the caller's identity from ``request`` and return a
        ``{"access_token": ...}`` payload.

        Strictness varies by impl:

        - **Local**: returns a mock token; no cookies are read. Local
          OpenClaw Gateway runs with ``auth.mode="none"``, nothing
          validates the token.
        - **Prod**: reads ``IAM_TOKEN`` cookie; raises
          ``ValidationError`` when missing; calls Buservice over HTTP to
          exchange the subject token for an access token.

        Raises:
            ValidationError: prod-impl, when the inbound cookie is
                missing or malformed.
            InternalError: prod-impl, on upstream failures.
        """
        ...
