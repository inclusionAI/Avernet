"""Unit tests for gateway principal verification (auth design §7.1, backend half).

Tokens are minted here with the same library and claim shape the gateway's
``BarePrincipalSigner`` uses, so these tests exercise the real contract rather
than a paraphrase of it. The forgery cases are the point of the file: each one is
a token an attacker could plausibly present, and each must be rejected.
"""

from __future__ import annotations

import time

import jwt
import pytest

from agentclaw.community.core.gateway_principal import (
    AccessKeyPrincipal,
    AppPrincipal,
    BotPrincipal,
    PrincipalVerificationError,
    PrincipalVerifierConfig,
    UserPrincipal,
    verify_principal_token,
)
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT

# At least 32 bytes: shorter HMAC keys are below the RFC 7518 recommendation for
# SHA-256 and PyJWT warns about them. Tests use a realistic key so the warning
# never becomes background noise that hides a real one.
KEY = "shared-hmac-secret-at-least-32-bytes-long"
AUDIENCE = "backend"
ISSUER = "gateway"
TENANT = "acme-tenant"

CONFIG = PrincipalVerifierConfig(signing_key=KEY, audience=AUDIENCE, issuer=ISSUER)


# ── payload builders (mirroring the gateway's model_dump(mode="json")) ────────


def user_principal(tenant: str = TENANT, user_id: str = "u-1") -> dict:
    return {
        "type": "user",
        "tenant": tenant,
        "subject": {
            "id": user_id,
            "username": "alice@example.com",
            "display_name": "Alice",
            "full_name": None,
            "tenant_id": tenant,
        },
    }


def bot_principal(tenant: str = TENANT, owner_id: str = "owner-9") -> dict:
    return {
        "type": "bot",
        "tenant": tenant,
        "bot": {
            "bot_uuid": "bot-abc",
            "owner_id": owner_id,
            "token": "SECRET-bot-session-token",
            "app_id": 7,
            "agent_code": "teclaw",
            "tenant": tenant,
        },
    }


def app_principal(tenant: str = TENANT) -> dict:
    return {
        "type": "app",
        "tenant": tenant,
        "app": {
            "app_id": 42,
            "app_name": "Partner App",
            "owners": "partner-org,someone-else",
            "tenant": tenant,
            "app_type": "THIRD_PARTY",
        },
    }


def access_key_principal(tenant: str = TENANT) -> dict:
    return {
        "type": "access_key",
        "tenant": tenant,
        "access_key": {
            "access_key": "ak-123",
            "access_key_token": "SECRET-access-key-token",
            "expire_at": "2030-01-01T00:00:00Z",
        },
    }


def mint(
    principals: list[dict],
    *,
    key: str = KEY,
    audience: str = AUDIENCE,
    issuer: str = ISSUER,
    ttl: int = 60,
    issued_at: int | None = None,
    algorithm: str = "HS256",
    omit: tuple[str, ...] = (),
) -> str:
    """Sign a principal payload exactly as the gateway's bare signer does."""
    now = int(time.time()) if issued_at is None else issued_at
    claims = {
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + ttl,
        "principals": principals,
    }
    for claim in omit:
        claims.pop(claim, None)
    return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": "bare"})


# ── happy paths ──────────────────────────────────────────────────────────────


def test_user_token_yields_tenant_and_owner():
    caller = verify_principal_token(mint([user_principal()]), CONFIG)

    assert caller.tenant == TENANT
    assert caller.user_id == "u-1"
    assert isinstance(caller.principals[0], UserPrincipal)


def test_bot_token_scopes_to_the_bots_owner():
    caller = verify_principal_token(mint([bot_principal()]), CONFIG)

    assert isinstance(caller.principals[0], BotPrincipal)
    assert caller.user_id == "owner-9"


def test_user_wins_over_app_when_both_identities_are_present():
    """A route may require a user and also accept an app; the person is the owner."""
    caller = verify_principal_token(mint([app_principal(), user_principal()]), CONFIG)

    assert len(caller.principals) == 2
    assert caller.user_id == "u-1"
    assert caller.tenant == TENANT


def test_user_wins_over_bot_when_both_identities_are_present():
    caller = verify_principal_token(mint([bot_principal(), user_principal()]), CONFIG)

    assert caller.user_id == "u-1"


def test_app_only_caller_yields_no_owner():
    """``app.owners`` is free-text org attribution, not a user id — never guessed."""
    caller = verify_principal_token(mint([app_principal()]), CONFIG)

    assert isinstance(caller.principals[0], AppPrincipal)
    assert caller.tenant == TENANT
    assert caller.user_id == ""


def test_access_key_only_caller_yields_no_owner():
    """The gateway's access-key registry has no owner column; nothing to scope to."""
    caller = verify_principal_token(mint([access_key_principal()]), CONFIG)

    assert isinstance(caller.principals[0], AccessKeyPrincipal)
    assert caller.tenant == TENANT
    assert caller.user_id == ""


def test_unknown_fields_do_not_break_verification():
    """The gateway must be able to add a field without taking this surface down."""
    payload = user_principal()
    payload["future_field"] = "whatever"
    payload["subject"]["future_subfield"] = 1

    caller = verify_principal_token(mint([payload]), CONFIG)

    assert caller.user_id == "u-1"


def test_forwarded_secrets_are_not_projected():
    """We are told the bot's session token and the access-key token; we drop both."""
    bot_caller = verify_principal_token(mint([bot_principal()]), CONFIG)
    key_caller = verify_principal_token(mint([access_key_principal()]), CONFIG)

    assert not hasattr(bot_caller.principals[0].bot, "token")
    assert "SECRET" not in bot_caller.principals[0].bot.model_dump_json()
    assert not hasattr(key_caller.principals[0].access_key, "access_key_token")
    assert "SECRET" not in key_caller.principals[0].access_key.model_dump_json()


def test_small_clock_skew_is_tolerated():
    """Two containers seconds apart must not reject each other's live tokens."""
    token = mint([user_principal()], ttl=1, issued_at=int(time.time()) - 4)

    assert verify_principal_token(token, CONFIG).user_id == "u-1"


# ── forgery and failure paths ────────────────────────────────────────────────


def test_wrong_signing_key_is_rejected():
    token = mint([user_principal()], key="not-the-shared-secret-but-also-32-bytes+")

    with pytest.raises(PrincipalVerificationError):
        verify_principal_token(token, CONFIG)


def test_unsigned_token_is_rejected():
    """``alg: none`` is the oldest JWT forgery; pinning algorithms defeats it."""
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
        "principals": [user_principal()],
    }
    token = jwt.encode(claims, key="", algorithm="none")

    with pytest.raises(PrincipalVerificationError):
        verify_principal_token(token, CONFIG)


def test_token_for_another_upstream_is_not_replayable_here():
    """A token the gateway minted for baas must not work against the backend."""
    token = mint([user_principal()], audience="baas")

    with pytest.raises(PrincipalVerificationError):
        verify_principal_token(token, CONFIG)


def test_token_from_another_issuer_is_rejected():
    token = mint([user_principal()], issuer="somebody-else")

    with pytest.raises(PrincipalVerificationError):
        verify_principal_token(token, CONFIG)


def test_expired_token_is_rejected():
    token = mint([user_principal()], ttl=60, issued_at=int(time.time()) - 3600)

    with pytest.raises(PrincipalVerificationError):
        verify_principal_token(token, CONFIG)


@pytest.mark.parametrize("claim", ["exp", "aud", "iss", "iat"])
def test_token_omitting_a_required_claim_is_rejected(claim: str):
    token = mint([user_principal()], omit=(claim,))

    with pytest.raises(PrincipalVerificationError):
        verify_principal_token(token, CONFIG)


def test_no_configured_key_rejects_everything():
    """An unconfigured deployment denies rather than accepting unsigned identity."""
    unconfigured = PrincipalVerifierConfig(
        signing_key="", audience=AUDIENCE, issuer=ISSUER
    )

    with pytest.raises(PrincipalVerificationError):
        verify_principal_token(mint([user_principal()]), unconfigured)


def test_empty_token_is_rejected():
    with pytest.raises(PrincipalVerificationError):
        verify_principal_token("", CONFIG)


def test_garbage_token_is_rejected():
    with pytest.raises(PrincipalVerificationError):
        verify_principal_token("not-a-jwt-at-all", CONFIG)


# ── payload-shape failures ───────────────────────────────────────────────────


def test_missing_principals_claim_is_rejected():
    token = jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
        },
        KEY,
        algorithm="HS256",
    )

    with pytest.raises(PrincipalVerificationError):
        verify_principal_token(token, CONFIG)


def test_empty_principal_set_is_rejected():
    """The gateway adds no header for an empty set, so an empty list is malformed."""
    with pytest.raises(PrincipalVerificationError):
        verify_principal_token(mint([]), CONFIG)


def test_non_list_principals_claim_is_rejected():
    token = jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "principals": {"type": "user"},
        },
        KEY,
        algorithm="HS256",
    )

    with pytest.raises(PrincipalVerificationError):
        verify_principal_token(token, CONFIG)


def test_unknown_principal_type_is_rejected():
    """An unrecognised tag must fail closed, not fall through to a member."""
    with pytest.raises(PrincipalVerificationError):
        verify_principal_token(mint([{"type": "root", "tenant": TENANT}]), CONFIG)


def test_renamed_contract_field_fails_closed():
    """If the gateway renames a required field we deny — we do not guess."""
    payload = user_principal()
    payload["subject"]["user_id"] = payload["subject"].pop("id")

    with pytest.raises(PrincipalVerificationError):
        verify_principal_token(mint([payload]), CONFIG)


# ── tenant integrity ─────────────────────────────────────────────────────────


def test_contradictory_tenants_are_rejected():
    """One request cannot belong to two tenants; picking one would invent an answer."""
    token = mint([user_principal(tenant="tenant-a"), app_principal(tenant="tenant-b")])

    with pytest.raises(PrincipalVerificationError):
        verify_principal_token(token, CONFIG)


def test_empty_tenant_is_rejected():
    with pytest.raises(PrincipalVerificationError):
        verify_principal_token(mint([user_principal(tenant="")]), CONFIG)


def test_internal_tenant_off_the_wire_is_rejected():
    """``teamclaw`` owns every internal row and must not be reachable publicly."""
    token = mint([user_principal(tenant=DEFAULT_AVERNET_TENANT)])

    with pytest.raises(PrincipalVerificationError) as exc:
        verify_principal_token(token, CONFIG)

    assert "internal tenant" in str(exc.value)
