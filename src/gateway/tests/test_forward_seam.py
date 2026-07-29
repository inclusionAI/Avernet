"""Tests for the forwarder identity seam (auth design §7.1).

The seam INJECTS the signed identity set as ``X-Avernet-Principal`` (the
PrincipalSigner workstream has landed). Components must verify the token —
never trust a bare header. Inbound ``X-Avernet-Principal`` is stripped at the
call site (covered by the integration test).
"""

from __future__ import annotations

import jwt

from gateway.community.adapters.web._forward import (
    _PRINCIPAL_HEADER,
    _attach_identities,
)
from gateway.community.spi.authn import AppPrincipal, ThirdPartyApp
from gateway.community.spi.forwarder import ForwardRequest


class _FixedSigner:
    """Signs with a fixed HMAC key for deterministic seam tests."""

    def __init__(self, key: str = "seam-key", *, kid: str = "bare") -> None:
        self._key = key
        self._kid = kid

    async def sign(self, principals, *, audience: str) -> str:
        payload = [p.model_dump(mode="json") for p in principals.values()]
        return jwt.encode(
            {"iss": "gateway", "aud": audience, "principals": payload},
            self._key,
            algorithm="HS256",
            headers={"kid": self._kid},
        )


def _app() -> AppPrincipal:
    return AppPrincipal(
        tenant="t",
        app=ThirdPartyApp(app_id="a", app_name="A", owners="o", tenant="t"),
    )


def _req() -> ForwardRequest:
    return ForwardRequest(
        method="GET", url="http://up/x", headers={"x-existing": "keep"}, content=b""
    )


async def test_seam_injects_signed_principal_header() -> None:
    out = await _attach_identities(
        _req(), {"app": _app()}, signer=_FixedSigner("k"), audience="secbaas"
    )
    token = out.headers[_PRINCIPAL_HEADER]
    decoded = jwt.decode(
        token, "k", algorithms=["HS256"], audience="secbaas", issuer="gateway"
    )
    assert decoded["aud"] == "secbaas"
    assert decoded["principals"] == [_app().model_dump(mode="json")]
    # The pre-existing header is preserved alongside the injected one.
    assert out.headers["x-existing"] == "keep"
    assert out.method == "GET"
    assert out.url == "http://up/x"


async def test_seam_no_header_when_identities_empty() -> None:
    out = await _attach_identities(
        _req(), {}, signer=_FixedSigner("k"), audience="secbaas"
    )
    assert _PRINCIPAL_HEADER not in out.headers
    assert out.headers == {"x-existing": "keep"}
