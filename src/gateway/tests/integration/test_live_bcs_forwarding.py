"""Opt-in proofs for the real Gateway-to-BCS HTTP and WebSocket boundaries."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gateway.community.adapters.web import WebsocketsForwarder
from gateway.community.adapters.web._forward import _ALL_METHODS, forward_request
from gateway.community.adapters.web._relay_ws import forward_websocket, relay_routes
from gateway.community.core.authn import RouteSecurity
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
LIVE_USER_ID = "gatewayliveuser"
_SESSION_WEBSOCKET_PATH = "/openapi/v1/collaboration/group/ws"
_SHIPPED_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "application.yaml"


class _ShippedRouteAuthenticator:
    """Use the shipped policy while supplying one deterministic live-test User."""

    def __init__(self) -> None:
        self._security = RouteSecurity.from_yaml(_SHIPPED_CONFIG)

    async def authenticate(
        self, method: str, path: str, credentials: object
    ) -> dict[PrincipalType, UserPrincipal]:
        requirement = self._security.resolve(method, path)
        if requirement is None:
            raise AssertionError(
                f"shipped route security has no policy for {method} {path}"
            )
        if requirement == {}:
            return {}
        return {
            PrincipalType.USER: UserPrincipal(
                tenant="gateway-live-test",
                subject=AuthenticatedUser(
                    id=LIVE_USER_ID,
                    username=LIVE_USER_ID,
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
            "domains": {
                "collaboration": {
                    "server": "bcs",
                    "protocols": ["http", "websocket"],
                }
            },
            "servers": {"bcs": {"base_url": url}},
        },
        variables={},
    )
    app.state.forwarder = HttpxForwarder(client=upstream)
    app.state.ws_forwarder = WebsocketsForwarder()
    app.state.authenticator = _ShippedRouteAuthenticator()
    app.state.principal_signer = BarePrincipalSigner(
        PrincipalSignerConfig(signing_key=DEVELOPMENT_SIGNING_KEY)
    )
    for route in relay_routes("/openapi/v1", "collaboration"):
        app.add_api_websocket_route(route, forward_websocket)
    app.add_api_route("/{full_path:path}", forward_request, methods=_ALL_METHODS)
    return app


def _prepare_live_session(url: str) -> str:
    """Arrange a real Human↔Bot DM session through BCS's legacy setup APIs."""
    identity_headers = {
        "X-Mock-User-Id": LIVE_USER_ID,
        "X-Mock-Nick-Name": "Gateway Live User",
    }
    with httpx.Client(base_url=url, timeout=10.0, trust_env=False) as bcs:
        ensure = bcs.post("/me/ensure-human", headers=identity_headers)
        assert ensure.status_code == 200, ensure.text

        registration = bcs.get("/register/token", headers=identity_headers)
        assert registration.status_code == 200, registration.text
        register_token = registration.json()["token"]

        registered = bcs.post(
            "/register",
            params={"token": register_token, "bot-name": "Gateway Live Driver"},
        )
        assert registered.status_code == 200, registered.text
        bot_id = registered.json()["bot_uuid"]

        group = bcs.post(
            "/groups",
            headers=identity_headers,
            json={
                "group_kind": "dm",
                "driver_bot": bot_id,
                "target_actor_id": bot_id,
                "participants": [{"bot_uuid": bot_id}],
            },
        )
        assert group.status_code == 200, group.text
        group_id = group.json()["id"]

        session = bcs.post(
            f"/groups/{quote(group_id, safe='')}/sessions",
            headers=identity_headers,
            json={
                "created_by": f"human_{LIVE_USER_ID}",
                "session_title": "Gateway live session",
            },
        )
        assert session.status_code == 201, session.text
        session_id = session.json()["session_id"]
        assert isinstance(session_id, str) and session_id
        return session_id


def test_gateway_forwards_signed_user_principal_to_live_bcs() -> None:
    """The real BCS V1 Router accepts Gateway's ``aud=bcs`` Principal."""
    with TestClient(_gateway_to(_live_bcs_url())) as client:
        response = client.get("/openapi/v1/collaboration/bots/mine")

    assert response.status_code == 200
    envelope = response.json()
    assert envelope["code"] == 20_000
    assert isinstance(envelope["data"], dict)


def test_gateway_issues_a_session_token_then_upgrades_the_live_bcs_websocket() -> None:
    """Cover #697 through Gateway up to connection/auth; message frames are out of scope."""
    bcs_url = _live_bcs_url()
    session_id = _prepare_live_session(bcs_url)

    with TestClient(_gateway_to(bcs_url)) as client:
        issued = client.post(
            f"/openapi/v1/collaboration/sessions/{quote(session_id, safe='')}/token"
        )

        assert issued.status_code == 200, issued.text
        assert issued.headers["cache-control"] == "no-store"
        token = issued.json()["data"]["token"]
        with client.websocket_connect(
            f"{_SESSION_WEBSOCKET_PATH}?token={quote(token, safe='')}"
        ) as websocket:
            websocket.close(1000)
