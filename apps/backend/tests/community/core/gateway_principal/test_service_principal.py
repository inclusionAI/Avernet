"""Unit tests for minting this component's own BCS service principal.

The minter is judged against **BCS's** published requirements — the
same wire contract the re-signer suite pins
(``apps/bcs/api-contracts/v1/gateway-principal/contract.md``, and BCN's
verifier: ``alg=HS256``, ``typ=JWT``, ``kid=bare``, ``aud=bcs``,
integer ``iat``/``exp`` with ``iat < exp``, a non-empty ``principals``
array) — plus the boundary that makes service minting safe at all:
the identity set is *fixed* to this component's app identity, the only
variable is the backend-owned tenant, and a missing signing key or a
blank tenant fails closed instead of producing a lesser credential.
"""

from __future__ import annotations

import time

import jwt
import pytest

from agentclaw.community.core.gateway_principal import (
    PrincipalSignerConfig,
    PrincipalVerificationError,
    mint_service_app_principal,
)
from tests.community.core.gateway_principal.test_verifier import KEY

# The envelope every token this component addresses to BCS carries —
# same constants as the re-signer suite's BCN_* pins.
BCN_AUDIENCE = "bcs"
BCN_ISSUER = "backend"
BCN_KEY_ID = "bare"

SIGNER = PrincipalSignerConfig(
    signing_key=KEY,
    audience=BCN_AUDIENCE,
    issuer=BCN_ISSUER,
    key_id=BCN_KEY_ID,
)

NO_KEY = PrincipalSignerConfig(
    signing_key="",
    audience=BCN_AUDIENCE,
    issuer=BCN_ISSUER,
    key_id=BCN_KEY_ID,
)


def _minted(tenant: str = "teamclaw", *, now: float | None = None) -> dict:
    token = mint_service_app_principal(SIGNER, tenant, now=now)
    return jwt.decode(
        token, KEY, algorithms=["HS256"], audience=BCN_AUDIENCE, issuer=BCN_ISSUER
    )


def test_minted_token_carries_the_bcs_envelope():
    token = mint_service_app_principal(SIGNER, "teamclaw")
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "HS256"
    assert header["typ"] == "JWT"
    assert header["kid"] == BCN_KEY_ID
    claims = jwt.decode(
        token, KEY, algorithms=["HS256"], audience=BCN_AUDIENCE, issuer=BCN_ISSUER
    )
    assert claims["iss"] == BCN_ISSUER
    assert claims["aud"] == BCN_AUDIENCE
    # Integer seconds, and a short window: minted per send, so ~the
    # gateway's own 60s default, never a long-lived credential.
    assert isinstance(claims["iat"], int)
    assert isinstance(claims["exp"], int)
    assert claims["exp"] - claims["iat"] == 60


def test_minted_identity_is_fixed_to_backend_and_the_given_tenant():
    claims = _minted("teamclaw")
    (principal,) = claims["principals"]
    assert principal["type"] == "app"
    assert principal["tenant"] == "teamclaw"
    app = principal["app"]
    # The byte-exact app BCS's trusted_apps rule must name.
    assert app["app_name"] == "backend"
    # BCS wires the outer tenant and the app tenant must agree — and a
    # forged app named "backend" scoped to another tenant is exactly
    # what the operator's per-tenant rule denies.
    assert app["tenant"] == "teamclaw"
    # Freely-shaped audit fields, all present and non-empty so BCS's
    # caller projection accepts the principal set.
    assert isinstance(app["app_id"], int)
    assert app["owners"]
    assert app["app_type"]


def test_minted_identity_is_not_caller_shaped():
    """The token asserts only the service itself — no user claim, ever.

    A ``principals`` set is a list, so its *length* is the full story of
    who the token speaks for: exactly one app principal, this component,
    nothing else — no room for a caller-supplied identity to hide in.
    """
    claims = _minted("teamclaw")
    assert len(claims["principals"]) == 1
    assert claims["principals"][0]["type"] == "app"


def test_mint_fails_closed_without_a_signing_key():
    with pytest.raises(PrincipalVerificationError, match="no principal signing key"):
        mint_service_app_principal(NO_KEY, "teamclaw")


@pytest.mark.parametrize("tenant", ["", "   "])
def test_mint_fails_closed_on_a_blank_tenant(tenant: str):
    with pytest.raises(PrincipalVerificationError, match="blank tenant"):
        mint_service_app_principal(SIGNER, tenant)


def test_token_expires_instead_of_living_forever():
    now = int(time.time())
    later = jwt.decode(
        mint_service_app_principal(SIGNER, "teamclaw", now=now),
        KEY,
        algorithms=["HS256"],
        audience=BCN_AUDIENCE,
        issuer=BCN_ISSUER,
        options={"verify_exp": False},
    )
    assert later["exp"] == now + 60
