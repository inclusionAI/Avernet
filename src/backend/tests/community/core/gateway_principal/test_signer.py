"""Unit tests for re-addressing a verified principal token to a second upstream.

The tests mint tokens the way the gateway's ``BarePrincipalSigner`` does (via
:func:`mint` from the verifier suite, so both files exercise one spelling of the
contract) and then judge the re-addressed copy against **BCN's** published
requirements — ``src/bcs/api-contracts/v1/gateway-principal/contract.md``:
``alg=HS256``, ``typ=JWT``, ``kid=bare``, ``aud=bcs``, integer ``iat``/``exp``,
a non-empty ``principals`` array.

``iss`` is the one field that is ours rather than BCN's. BCN checks it against
the issuer it is configured to trust, and for a token this component signs that
is this component's own name — the gateway's ``servers:`` key for us,
``backend`` — not the gateway's. The assertions here pin that value, so a change
of who we claim to be cannot pass unnoticed on the side that has to trust it.

The rest of the contract is the whole reason this module exists, so the
assertions are deliberately literal about it: a re-addressed token that
satisfies these checks is one BCN accepts, and one that does not is the bug this
fixes coming back.
"""

from __future__ import annotations

import time

import jwt
import pytest

from agentclaw.community.core.gateway_principal import (
    PrincipalSignerConfig,
    PrincipalVerificationError,
    PrincipalVerifierConfig,
    resign_principal_token,
)
from tests.community.core.gateway_principal.test_verifier import (
    AUDIENCE,
    ISSUER,
    KEY,
    TENANT,
    access_key_principal,
    app_principal,
    bot_principal,
    mint,
    user_principal,
)

# The envelope a token addressed to BCN carries. ``aud``/``kid`` mirror BCN's
# shipped ``gateway_principal.{audience,key_id}``; ``iss`` is this component's
# own name, which BCN has to be configured to trust.
BCN_AUDIENCE = "bcs"
BCN_ISSUER = "backend"
BCN_KEY_ID = "bare"

VERIFIER = PrincipalVerifierConfig(signing_key=KEY, audience=AUDIENCE, issuer=ISSUER)
SIGNER = PrincipalSignerConfig(
    signing_key=KEY,
    audience=BCN_AUDIENCE,
    issuer=BCN_ISSUER,
    key_id=BCN_KEY_ID,
)


def bcn_verify(token: str, *, key: str = KEY) -> dict:
    """Decode ``token`` the way BCN does, and fail the test if BCN would not.

    Not a paraphrase of BCN's verifier — the claim checks it can express in
    PyJWT are asserted here, and the JOSE header checks BCN makes before it ever
    looks at the signature are asserted by the caller.
    """
    return jwt.decode(
        token,
        key,
        algorithms=["HS256"],
        audience=BCN_AUDIENCE,
        issuer=BCN_ISSUER,
        options={"require": ["exp", "iat", "iss", "aud"]},
    )


# ── the re-addressing itself ─────────────────────────────────────────────────


def test_readdressed_token_satisfies_the_bcn_contract():
    """The point of the module: BCN accepts what the backend could not forward.

    ``iss`` names this component, not the gateway — the claims are still the
    gateway's assertions, but the signature over this copy is the backend's.
    """
    original = mint([user_principal()])

    readdressed = resign_principal_token(original, verifier=VERIFIER, signer=SIGNER)

    header = jwt.get_unverified_header(readdressed)
    assert header["alg"] == "HS256"
    assert header["typ"] == "JWT"
    assert header["kid"] == BCN_KEY_ID
    claims = bcn_verify(readdressed)
    assert claims["iss"] == BCN_ISSUER
    assert claims["aud"] == BCN_AUDIENCE
    assert isinstance(claims["iat"], int)
    assert isinstance(claims["exp"], int)


def test_the_inbound_token_is_the_one_bcn_refuses():
    """The failure this fixes, asserted rather than assumed.

    Without it the suite could pass while the header the backend actually
    forwards is still addressed to ``backend`` — which is exactly the state the
    friend-approval callback was in.

    Both halves of the envelope are wrong on the inbound token: it is addressed
    to us rather than to BCN, and it was issued by the gateway rather than by
    the component making this call. PyJWT reports whichever it checks first, so
    the assertion names the pair.
    """
    with pytest.raises((jwt.InvalidAudienceError, jwt.InvalidIssuerError)):
        bcn_verify(mint([user_principal()]))


def test_identities_and_lifetime_survive_the_re_addressing():
    """Same caller, same window — only the envelope changes.

    BCN authorizes the approval against the identity set, so a re-addressing
    that altered it would apply the decision as somebody else. And ``exp`` is
    the gateway's 60-second budget: re-stamping it here would silently mint a
    longer-lived credential on every hop.
    """
    issued_at = int(time.time()) - 5
    original = mint(
        [app_principal(), user_principal()], ttl=60, issued_at=issued_at
    )

    claims = bcn_verify(
        resign_principal_token(original, verifier=VERIFIER, signer=SIGNER)
    )

    assert claims["principals"] == [app_principal(), user_principal()]
    assert claims["iat"] == issued_at
    assert claims["exp"] == issued_at + 60


def test_unknown_claims_are_carried_across():
    """A claim we do not read may still be one BCN does; dropping it is not ours."""
    now = int(time.time())
    original = jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + 60,
            "principals": [user_principal()],
            "trace_id": "gateway-trace-1",
        },
        KEY,
        algorithm="HS256",
        headers={"kid": "bare"},
    )

    claims = bcn_verify(
        resign_principal_token(original, verifier=VERIFIER, signer=SIGNER)
    )

    assert claims["trace_id"] == "gateway-trace-1"


def test_the_copy_is_signed_with_the_shared_key_only():
    """A different key produces a token BCN rejects — the key is the contract."""
    readdressed = resign_principal_token(
        mint([user_principal()]), verifier=VERIFIER, signer=SIGNER
    )

    with pytest.raises(jwt.InvalidSignatureError):
        bcn_verify(readdressed, key="a-different-secret-of-at-least-32-bytes!!")


# ── nothing is signed that was not verified first ────────────────────────────


# ``ids`` is pinned to the label on purpose: the token values are minted at
# *collection* time (JWT iat/exp differ by the second), so the default
# id-by-repr makes pytest-xdist's cross-worker collection check see different
# test sets whenever workers finish collecting more than a second apart.
@pytest.mark.parametrize(
    ("label", "token"),
    [
        ("forged signature", mint([user_principal()], key="attacker-supplied-key")),
        ("expired", mint([user_principal()], ttl=-3600)),
        ("minted for another upstream", mint([user_principal()], audience="bcs")),
        ("wrong issuer", mint([user_principal()], issuer="not-the-gateway")),
        ("no principals claim", mint([user_principal()], omit=("principals",))),
        ("empty", ""),
    ],
    ids=[
        "forged-signature",
        "expired",
        "another-upstream",
        "wrong-issuer",
        "no-principals-claim",
        "empty",
    ],
)
def test_a_token_that_does_not_verify_is_never_re_addressed(label: str, token: str):
    """Re-addressing must not be a way to launder a token into a valid one.

    Every case here is a credential this component would answer ``401`` to. If
    any of them came back signed with the shared key, the backend would be a
    token-minting oracle for every upstream that trusts it.
    """
    with pytest.raises(PrincipalVerificationError):
        resign_principal_token(token, verifier=VERIFIER, signer=SIGNER)


def test_an_identity_set_this_surface_refuses_is_never_re_addressed():
    """The admission check runs before signing, not only at the HTTP seam.

    An access-key-only set names no end user and no application, so no public
    route would serve it. Handing BCN a caller we ourselves refuse would move
    that decision to a component that cannot see our route table.
    """
    with pytest.raises(PrincipalVerificationError, match="neither a user nor an app"):
        resign_principal_token(
            mint([access_key_principal()]), verifier=VERIFIER, signer=SIGNER
        )


def test_a_contradictory_tenant_is_refused_before_signing():
    """Two tenants in one set is unresolvable here and would be there too."""
    with pytest.raises(PrincipalVerificationError, match="mixes 2 tenants"):
        resign_principal_token(
            mint([user_principal(), bot_principal(tenant="other-tenant"),
                  app_principal(tenant=TENANT)]),
            verifier=VERIFIER,
            signer=SIGNER,
        )


def test_no_signing_key_refuses_rather_than_signing_with_nothing():
    """An unconfigured deployment produces no credential at all.

    Same reading as the verifier's empty key, from the signing side: a token
    signed with ``""`` is not a weaker credential, it is a forgeable one.
    """
    with pytest.raises(PrincipalVerificationError, match="no principal signing key"):
        resign_principal_token(
            mint([user_principal()]),
            verifier=VERIFIER,
            signer=PrincipalSignerConfig(
                signing_key="",
                audience=BCN_AUDIENCE,
                issuer=BCN_ISSUER,
                key_id=BCN_KEY_ID,
            ),
        )


def test_no_verifier_key_refuses_even_when_the_signer_has_one():
    """Both halves are needed: nothing verifies, so nothing is worth signing."""
    with pytest.raises(PrincipalVerificationError, match="no principal signing key"):
        resign_principal_token(
            mint([user_principal()]),
            verifier=PrincipalVerifierConfig(
                signing_key="", audience=AUDIENCE, issuer=ISSUER
            ),
            signer=SIGNER,
        )


def test_the_signer_config_fingerprints_its_key_for_a_log():
    """Boot lines compare fingerprints across components, never the key itself."""
    assert SIGNER.key_fingerprint != "unset"
    assert KEY not in SIGNER.key_fingerprint
    assert (
        PrincipalSignerConfig(
            signing_key="",
            audience=BCN_AUDIENCE,
            issuer=BCN_ISSUER,
            key_id=BCN_KEY_ID,
        ).key_fingerprint
        == "unset"
    )
