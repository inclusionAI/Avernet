"""Integration: the forward path signs the resolved identity and strips inbound fakes."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import asynccontextmanager

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from gateway.community.adapters.web.app import create_app
from gateway.community.plugins.principal_signer.bare import (
    BarePrincipalSigner,
    PrincipalSignerConfig,
)
from gateway.community.spi.authn import (
    AppPrincipal,
    Principal,
    PrincipalType,
    ThirdPartyApp,
)
from gateway.community.spi.forwarder import ForwardRequest, ForwardResponse

_PRINCIPAL_HEADER = "X-Avernet-Principal"

# The signing key this test's app is given, explicitly. It used to decode with
# the plugin's dev fallback, which coupled the test to a committed credential
# and to the gateway booting with one; the fallback is gone, so the test
# provisions a key the way a deployment does.
_TEST_KEY = "integration-test-shared-secret-32b!!"


class _StubAuthenticator:
    """Returns a fixed app principal for any call; route_security unused here."""

    async def authenticate(
        self, method: str, path: str, creds: object
    ) -> dict[PrincipalType, Principal]:
        return {
            PrincipalType.APP: AppPrincipal(
                tenant="t",
                app=ThirdPartyApp(app_id=1, app_name="A", owners="o", tenant="t"),
            )
        }


class _CapturingForwarder:
    """Captures the outbound ForwardRequest; responds 200 with an empty body."""

    def __init__(self) -> None:
        self.captured: ForwardRequest | None = None

    @asynccontextmanager
    async def forward(self, request: ForwardRequest):
        self.captured = request

        async def _empty_body():
            if False:  # pragma: no cover
                yield

        yield ForwardResponse(status_code=200, headers=[], body=_empty_body())


class _BoomSigner:
    async def sign(
        self, principals: Mapping[PrincipalType, Principal], *, audience: str
    ) -> str:
        raise RuntimeError("boom")


@pytest.fixture
def app_with_capture() -> tuple:
    app = create_app()
    forwarder = _CapturingForwarder()
    app.state.authenticator = _StubAuthenticator()
    app.state.forwarder = forwarder
    app.state.principal_signer = BarePrincipalSigner(
        PrincipalSignerConfig(signing_key=_TEST_KEY)
    )
    return app, forwarder


async def test_forward_signs_principal_with_server_audience(
    app_with_capture: tuple,
) -> None:
    app, forwarder = app_with_capture
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/openapi/v1/bots/x")

    assert resp.status_code == 200
    assert forwarder.captured is not None
    token = forwarder.captured.headers[_PRINCIPAL_HEADER]
    # `bots` domain -> server "backend" (configs/upstreams.yaml).
    decoded = jwt.decode(
        token,
        _TEST_KEY,
        algorithms=["HS256"],
        audience="backend",
        issuer="gateway",
    )
    assert decoded["aud"] == "backend"
    assert len(decoded["principals"]) == 1
    assert decoded["principals"][0]["type"] == "app"
    assert decoded["principals"][0]["tenant"] == "t"


async def test_forward_strips_inbound_principal_header(
    app_with_capture: tuple,
) -> None:
    app, forwarder = app_with_capture
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/openapi/v1/bots/x",
            headers={_PRINCIPAL_HEADER: "forged-by-caller"},
        )

    assert resp.status_code == 200
    assert forwarder.captured is not None
    principal_headers = [
        v for k, v in forwarder.captured.headers.items() if k == _PRINCIPAL_HEADER
    ]
    assert len(principal_headers) == 1  # exactly one, and not the forged value
    assert principal_headers[0] != "forged-by-caller"


async def test_forward_returns_500_when_signing_fails() -> None:
    app = create_app()
    app.state.authenticator = _StubAuthenticator()
    app.state.forwarder = _CapturingForwarder()
    app.state.principal_signer = _BoomSigner()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/openapi/v1/bots/x")

    assert resp.status_code == 500


async def test_forward_returns_500_when_no_signing_key_is_configured() -> None:
    """The end state of removing the dev fallback, seen from the wire.

    A gateway with no key used to sign with a committed constant and forward
    happily; the failure then surfaced as a 401 from an upstream, naming
    neither this component nor the cause. Now it fails here, as a 500 whose log
    line points at the unprovisioned secret.
    """
    app = create_app()
    app.state.authenticator = _StubAuthenticator()
    app.state.forwarder = _CapturingForwarder()
    app.state.principal_signer = BarePrincipalSigner(
        PrincipalSignerConfig(signing_key="")
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/openapi/v1/bots/x")

    assert resp.status_code == 500


async def test_an_unsigned_token_is_never_forwarded() -> None:
    """The property the 500 protects: no header rather than a forgeable one.

    HMAC over an empty key produces a signature anyone can reproduce, so
    emitting it would be strictly worse than emitting nothing.
    """
    app = create_app()
    forwarder = _CapturingForwarder()
    app.state.authenticator = _StubAuthenticator()
    app.state.forwarder = forwarder
    app.state.principal_signer = BarePrincipalSigner(
        PrincipalSignerConfig(signing_key="")
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/openapi/v1/bots/x")

    assert forwarder.captured is None, "the request never reached the upstream"
