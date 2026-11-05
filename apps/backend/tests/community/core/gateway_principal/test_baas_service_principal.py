"""Unit tests for minting the app principal this component presents to BaaS.

BaaS's verifier (``apps/baas/.../api/api_gateway/_jwt.py``) differs from BCS's
in one important way: it decodes with ``verify_aud=False`` and then reads only
two claims — ``principals[0].access_key.access_key`` and
``principals[0].tenant``. So the properties that actually matter here are

1. the resource key sits exactly where BaaS looks for it,
2. the tenant matches the row's tenant (BaaS scopes its lookup by both), and
3. a missing signing key, tenant or resource key fails closed before a request
   is sent, instead of producing a credential BaaS would reject at the far end.

The ``aud``/``iss``/``kid`` envelope is still pinned: BaaS ignores it today, and
this test is what keeps it coherent if that ever changes.
"""

from __future__ import annotations

import time

import jwt
import pytest

from agentclaw.community.core.gateway_principal import (
    PrincipalSignerConfig,
    PrincipalVerificationError,
)
from agentclaw.community.core.gateway_principal.baas_service_principal import (
    mint_baas_service_app_principal,
)
from tests.community.core.gateway_principal.test_verifier import KEY

# The envelope this component addresses to BaaS.
BAAS_AUDIENCE = "baas"
BAAS_ISSUER = "backend"
BAAS_KEY_ID = "bare"

SIGNER = PrincipalSignerConfig(
    signing_key=KEY,
    audience=BAAS_AUDIENCE,
    issuer=BAAS_ISSUER,
    key_id=BAAS_KEY_ID,
)

TENANT = "teamclaw"
RESOURCE_KEY = "agent_swarm_agent_teamclaw"


def _mint(**overrides):
    kwargs = {"signer": SIGNER, "tenant": TENANT, "resource_key": RESOURCE_KEY}
    kwargs.update(overrides)
    return mint_baas_service_app_principal(**kwargs)


def _decode(token: str) -> dict:
    """Decode the way BaaS does: signature checked, audience not."""
    return jwt.decode(token, KEY, algorithms=["HS256"], options={"verify_aud": False})


def _decode_past(token: str) -> dict:
    """Decode a token minted at a pinned past instant, without expiry checks.

    Used by the clock-override test: the point there is the claim values, not
    whether a token for 1970 is still live.
    """
    return jwt.decode(
        token,
        KEY,
        algorithms=["HS256"],
        options={"verify_aud": False, "verify_exp": False},
    )


class TestBaaSReadsTheseClaims:
    """The two claims BaaS actually authorizes against."""

    def test_resource_key_is_under_access_key(self):
        claims = _decode(_mint())

        assert claims["principals"][0]["access_key"]["access_key"] == RESOURCE_KEY

    def test_tenant_is_on_the_principal(self):
        claims = _decode(_mint())

        assert claims["principals"][0]["tenant"] == TENANT

    def test_principal_names_an_app(self):
        claims = _decode(_mint())

        assert claims["principals"][0]["type"] == "app"

    def test_there_is_exactly_one_principal(self):
        # A second identity would be a statement this component has no basis
        # to make: BaaS reads principals[0] and only principals[0].
        assert len(_decode(_mint())["principals"]) == 1


class TestEnvelope:
    """Ignored by BaaS today; pinned so it stays honest."""

    def test_audience_names_baas(self):
        assert _decode(_mint())["aud"] == BAAS_AUDIENCE

    def test_issuer_names_this_component(self):
        assert _decode(_mint())["iss"] == BAAS_ISSUER

    def test_algorithm_is_hs256(self):
        assert jwt.get_unverified_header(_mint())["alg"] == "HS256"

    def test_key_id_is_carried(self):
        assert jwt.get_unverified_header(_mint())["kid"] == BAAS_KEY_ID

    def test_ttl_is_sixty_seconds(self):
        claims = _decode(_mint())

        assert claims["exp"] - claims["iat"] == 60

    def test_times_are_integers(self):
        # BaaS runs the same PyJWT decode; fractional timestamps fail there.
        claims = _decode(_mint())

        assert isinstance(claims["iat"], int)
        assert isinstance(claims["exp"], int)

    def test_now_overrides_the_clock(self):
        claims = _decode_past(_mint(now=1_000_000))

        assert claims["iat"] == 1_000_000
        assert claims["exp"] == 1_000_060

    def test_a_fresh_token_is_not_expired(self):
        claims = _decode(_mint())

        assert claims["exp"] > time.time()


class TestFailsClosed:
    """Blank inputs are refused here, not at the far end."""

    def test_blank_signing_key_is_refused(self):
        signer = PrincipalSignerConfig(
            signing_key="", audience=BAAS_AUDIENCE, issuer=BAAS_ISSUER,
            key_id=BAAS_KEY_ID,
        )
        with pytest.raises(PrincipalVerificationError, match="no principal signing key"):
            _mint(signer=signer)

    def test_blank_tenant_is_refused(self):
        with pytest.raises(PrincipalVerificationError, match="blank tenant"):
            _mint(tenant="   ")

    def test_empty_tenant_is_refused(self):
        with pytest.raises(PrincipalVerificationError, match="blank tenant"):
            _mint(tenant="")

    def test_blank_resource_key_is_refused(self):
        with pytest.raises(PrincipalVerificationError, match="blank resource key"):
            _mint(resource_key="")

    def test_whitespace_resource_key_is_refused(self):
        with pytest.raises(PrincipalVerificationError, match="blank resource key"):
            _mint(resource_key="  \t ")

    def test_surrounding_whitespace_is_trimmed(self):
        claims = _decode(_mint(tenant=" teamclaw ", resource_key=" rk "))

        assert claims["principals"][0]["tenant"] == "teamclaw"
        assert claims["principals"][0]["access_key"]["access_key"] == "rk"


class TestSignatureIsBoundToTheKey:
    """A token minted with one key must not verify with another."""

    def test_wrong_key_fails_to_decode(self):
        token = _mint()

        with pytest.raises(jwt.InvalidSignatureError):
            jwt.decode(token, "z" * 64, algorithms=["HS256"], options={"verify_aud": False})