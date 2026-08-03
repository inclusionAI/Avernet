"""Opt-in proof that Gateway forwards a signed User principal to live BCS."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gateway.community.adapters.web._forward import _ALL_METHODS, forward_request
from gateway.community.core.forwarding import DomainMap
from gateway.community.plugins.forwarder.httpx import HttpxForwarder
from gateway.community.plugins.principal_signer.bare import (
    BarePrincipalSigner,
    PrincipalSignerConfig,
)
from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn import PrincipalType, UserPrincipal

pytestmark = pytest.mark.integration

DEVELOPMENT_SIGNING_KEY = "avernet-dev-signing-key-NOT-FOR-PROD"


class _UserAuthenticator:
    """Minimal Gateway authenticator that produces a real User principal."""

    async def authenticate(
        self, method: str, path: str, credentials: object
    ) -> dict[PrincipalType, UserPrincipal]:
        return {
            PrincipalType.USER: UserPrincipal(
                tenant="gateway-live-test",
                subject=AuthenticatedUser(
                    id="gateway-live-user",
                    username="gateway-live-user",
                    tenant_id="gateway-live-test",
                ),
            )
        }


def _live_bcs_url() -> str:
    if os.environ.get("GATEWAY_LIVE_BCS") != "1":
        pytest.skip("set GATEWAY_LIVE_BCS=1 via scripts/test_live_bcs_forwarding.sh")
    return os.environ["GATEWAY_LIVE_BCS_URL"]


def _gateway_to(url: str) -> FastAPI:
    upstream = httpx.AsyncClient(timeout=10.0, trust_env=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await upstream.aclose()

    app = FastAPI(lifespan=lifespan)
    app.state.domain_map = DomainMap.from_config(
        {
            "domains": {"collaboration": {"server": "bcs"}},
            "servers": {"bcs": {"base_url": url}},
        },
        variables={},
    )
    app.state.forwarder = HttpxForwarder(client=upstream)
    app.state.authenticator = _UserAuthenticator()
    app.state.principal_signer = BarePrincipalSigner(
        PrincipalSignerConfig(signing_key=DEVELOPMENT_SIGNING_KEY)
    )
    app.add_api_route("/{full_path:path}", forward_request, methods=_ALL_METHODS)
    return app


def test_gateway_forwards_signed_user_principal_to_live_bcs() -> None:
    """The real BCS V1 Router accepts Gateway's ``aud=bcs`` Principal."""
    with TestClient(_gateway_to(_live_bcs_url())) as client:
        response = client.get("/openapi/v1/collaboration/bots/mine")

    assert response.status_code == 200
    envelope = response.json()
    assert envelope["code"] == 20_000
    assert isinstance(envelope["data"], dict)
