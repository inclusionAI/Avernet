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
    key_fingerprint,
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


def user_principal(user_id: str = "u-1", subject_tenant: str = TENANT) -> dict:
    """A ``user`` principal as the gateway serializes it — with no tenant.

    ``subject.tenant_id`` is the identity provider's claim about the person and
    is carried for attribution only; nothing scopes by it. The principal itself
    asserts no tenant, because nothing in a user credential proves one.
    """
    return {
        "type": "user",
        "subject": {
            "id": user_id,
            "username": "alice@example.com",
            "display_name": "Alice",
            "full_name": None,
            "tenant_id": subject_tenant,
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


def test_user_only_token_yields_the_internal_tenant_and_the_owner():
    """A caller presenting nothing but a first-party user identity is internal.

    The user principal asserts no tenant, so there is nothing to scope by off
    the wire and the internal default applies — the same tenant every non-public
    path in this component resolves to.
    """
    caller = verify_principal_token(mint([user_principal()]), CONFIG)

    assert caller.tenant == DEFAULT_AVERNET_TENANT
    assert caller.user_id == "u-1"
    assert isinstance(caller.principals[0], UserPrincipal)


def test_user_wins_over_app_when_both_identities_are_present():
    """A route may require a user and also accept an app; the person is the owner."""
    caller = verify_principal_token(mint([app_principal(), user_principal()]), CONFIG)

    assert len(caller.principals) == 2
    # The extra identity is carried, not dropped: admission requires a user, it
    # does not strip everything else out of the set.
    assert isinstance(caller.principals[0], AppPrincipal)
    assert caller.user_id == "u-1"
    assert caller.tenant == TENANT


def test_user_wins_over_bot_when_both_identities_are_present():
    """A bot alongside a user is carried, not refused — the person is the owner."""
    caller = verify_principal_token(mint([bot_principal(), user_principal()]), CONFIG)

    assert len(caller.principals) == 2
    assert isinstance(caller.principals[0], BotPrincipal)
    assert caller.user_id == "u-1"


def test_access_key_alongside_a_user_is_carried():
    """The gateway forwards every identity it resolved; only the user is required."""
    caller = verify_principal_token(
        mint([access_key_principal(), user_principal()]), CONFIG
    )

    assert isinstance(caller.principals[0], AccessKeyPrincipal)
    assert caller.user_id == "u-1"


# ── identity-set admission (only callers that name an end user) ───────────────


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("app", app_principal()),
        ("access_key", access_key_principal()),
        ("bot", bot_principal()),
    ],
)
def test_a_set_naming_no_end_user_is_refused(label: str, payload: dict):
    """Refused at verification, so no handler can be reached with an unscopeable caller.

    ``app.owners`` is free-text org attribution and the access-key registry has
    no owner column, so neither names a person. ``bot`` does carry ``owner_id``,
    but a bot acting as its own owner across the public contract is a grant
    nobody made — see ``_require_user_principal``.
    """
    with pytest.raises(PrincipalVerificationError, match="no user identity"):
        verify_principal_token(mint([payload]), CONFIG)


def test_a_set_of_several_non_user_identities_is_refused():
    """Two identities that each name no person still name no person."""
    with pytest.raises(PrincipalVerificationError, match="no user identity"):
        verify_principal_token(
            mint([app_principal(), access_key_principal()]), CONFIG
        )


def test_the_refusal_names_the_types_carried_for_the_operator():
    """The log line has to be diagnosable; the caller still sees one fixed 401."""
    with pytest.raises(PrincipalVerificationError) as exc_info:
        verify_principal_token(
            mint([access_key_principal(), app_principal()]), CONFIG
        )

    assert "access_key" in str(exc_info.value)
    assert "app" in str(exc_info.value)


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_a_user_with_a_blank_subject_id_is_refused(blank: str):
    """A type check alone is not enough — both sides model the id as a bare ``str``.

    Reachable rather than theoretical: the gateway's google strategy reads
    ``body["sub"]``, which raises on a *missing* claim but passes an empty one
    through. Such a caller names an owner no better than an access key does, and
    the handlers that never call ``caller_owner_id`` would run for it.
    """
    with pytest.raises(PrincipalVerificationError, match="blank subject id"):
        verify_principal_token(mint([user_principal(user_id=blank)]), CONFIG)


def test_a_blank_first_user_is_refused_even_behind_a_usable_one():
    """The admission check and ``user_id`` must agree on *which* user is the owner.

    The gateway resolves at most one identity per type, but ``principals`` is a
    list, so a token can present two users. Asking "does some user have an id?"
    while deriving the owner from the *first* user would admit this set and then
    scope by nothing at all.
    """
    token = mint([user_principal(user_id=""), user_principal(user_id="u-2")])

    with pytest.raises(PrincipalVerificationError, match="blank subject id"):
        verify_principal_token(token, CONFIG)


def test_a_blank_subject_id_is_refused_distinctly_from_a_missing_user():
    """Two different operator diagnoses, so two different messages."""
    with pytest.raises(PrincipalVerificationError) as blank:
        verify_principal_token(mint([user_principal(user_id="")]), CONFIG)
    with pytest.raises(PrincipalVerificationError) as missing:
        verify_principal_token(mint([app_principal()]), CONFIG)

    assert "no user identity" not in str(blank.value)
    assert "blank subject id" not in str(missing.value)


def test_unknown_fields_do_not_break_verification():
    """The gateway must be able to add a field without taking this surface down."""
    payload = user_principal()
    payload["future_field"] = "whatever"
    payload["subject"]["future_subfield"] = 1

    caller = verify_principal_token(mint([payload]), CONFIG)

    assert caller.user_id == "u-1"


def test_forwarded_secrets_are_not_projected():
    """We are told the bot's session token and the access-key token; we drop both.

    Each is minted alongside a user because a set naming no end user is now
    refused before projection — the secrets still ride the wire, so dropping
    them is still this module's job.
    """
    bot_caller = verify_principal_token(
        mint([bot_principal(), user_principal()]), CONFIG
    )
    key_caller = verify_principal_token(
        mint([access_key_principal(), user_principal()]), CONFIG
    )

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
    token = mint(
        [
            user_principal(),
            app_principal(tenant="tenant-a"),
            bot_principal(tenant="tenant-b"),
        ]
    )

    with pytest.raises(PrincipalVerificationError):
        verify_principal_token(token, CONFIG)


def test_empty_tenant_is_rejected():
    token = mint([user_principal(), app_principal(tenant="")])

    with pytest.raises(PrincipalVerificationError):
        verify_principal_token(token, CONFIG)


def test_internal_tenant_named_on_the_wire_is_honoured():
    """``teamclaw`` is a routable tenant claim. *Changed 2026-08-05.*

    Verification used to refuse a token whose machine principal named the
    internal tenant, on the reasoning that no gateway tenant was spelled that way
    and honouring one would scope an external caller to every pre-existing
    internal row. An app registered to ``teamclaw`` is now a deliberate
    first-party path through the gateway, so the claim is treated like any other
    tenant's: the gateway vouched for the registration, and this component
    believes what it vouches for.

    The tenant a request scopes to is still decided by one rule, not two — a
    named ``teamclaw`` and the user-only fallback
    (:func:`test_user_only_token_yields_the_internal_tenant_and_the_owner`) land
    on the same value by different routes.
    """
    token = mint([user_principal(), app_principal(tenant=DEFAULT_AVERNET_TENANT)])

    caller = verify_principal_token(token, CONFIG)

    assert caller.tenant == DEFAULT_AVERNET_TENANT
    assert caller.user_id == "u-1"


def test_internal_tenant_still_cannot_be_mixed_with_another():
    """Honouring ``teamclaw`` did not exempt it from the one-tenant rule.

    The contradiction guard judges tenants by count, not by name, so a set that
    pairs the internal tenant with an external one is refused for the same reason
    any other mixed set is: picking one would invent an answer the gateway never
    gave.
    """
    token = mint(
        [
            user_principal(),
            app_principal(tenant=DEFAULT_AVERNET_TENANT),
            bot_principal(tenant="acme-partner"),
        ]
    )

    with pytest.raises(PrincipalVerificationError):
        verify_principal_token(token, CONFIG)


def test_a_tenant_claimed_on_a_user_principal_is_ignored():
    """An old-contract or forged token cannot scope itself by naming a tenant.

    The DTO ignores unknown fields so the gateway can add one without breaking
    us, which means a ``tenant`` on a ``user`` entry is dropped rather than
    rejected. Dropping is the safe direction: the claim buys nothing, and the
    caller lands on the internal default like any other user-only request.
    """
    payload = user_principal()
    payload["tenant"] = "tenant-the-caller-would-like"

    caller = verify_principal_token(mint([payload]), CONFIG)

    assert caller.tenant == DEFAULT_AVERNET_TENANT


def test_a_user_alongside_an_app_takes_the_apps_tenant():
    """The machine identity is the one that carries a registered tenant."""
    caller = verify_principal_token(
        mint([user_principal(), app_principal(tenant="acme-partner")]), CONFIG
    )

    assert caller.tenant == "acme-partner"
    assert caller.user_id == "u-1"


# ── operator diagnostics ─────────────────────────────────────────────────────
#
# A rejected token answers one fixed 401, so the *log* is the only place the
# reason survives. These tests pin what that log line must carry, because the
# failure they exist for — the gateway and this component holding different
# shared secrets — is otherwise indistinguishable from a forgery.


def test_key_fingerprint_matches_the_gateway_golden_values():
    """The fingerprint is a cross-component contract, pinned by literal value.

    The gateway computes the same digest over its own copy of the key and both
    sides log it at boot; comparing the two lines is how an operator tells "we
    hold different secrets" from "the secret is stale". The two implementations
    live in separate distributions with no shared package, so nothing but this
    test and its twin in ``src/gateway/tests/unit/plugins/test_principal_signer
    .py`` stops one side from drifting and quietly making the comparison
    meaningless. Change these expected values only when both change together.
    """
    assert key_fingerprint("k" * 32) == "5e318f8c"
    assert key_fingerprint("a-shared-secret-of-at-least-32-bytes!!") == "eb128a7a"
    assert key_fingerprint("rotated-shared-secret-32-bytes-min!!!!") == "21654248"


def test_fingerprint_of_no_key_reads_as_unset():
    """Never a hash of the empty string, which would look like a real key."""
    assert key_fingerprint("") == "unset"


def test_fingerprint_does_not_leak_the_key():
    assert KEY not in key_fingerprint(KEY)
    assert len(key_fingerprint(KEY)) == 8


def test_config_exposes_its_own_fingerprint():
    assert CONFIG.key_fingerprint == key_fingerprint(KEY)


def test_a_key_mismatch_is_diagnosable_from_the_message_alone():
    """The failure this whole section exists for.

    ``Signature verification failed`` on its own cannot say which key the token
    was judged against. The suffix must name it, alongside the contract this
    component enforces — all of it read from our own configuration.
    """
    token = mint([user_principal()], key="not-the-shared-secret-but-also-32-bytes+")

    with pytest.raises(PrincipalVerificationError) as exc:
        verify_principal_token(token, CONFIG)

    message = str(exc.value)
    assert "Signature verification failed" in message, "PyJWT's own reason survives"
    assert f"verifier key fp={key_fingerprint(KEY)}" in message
    assert f"aud={AUDIENCE!r}" in message and f"iss={ISSUER!r}" in message
    assert "alg='HS256'" in message
    assert "kid='bare'" in message


def test_the_jose_header_is_labelled_as_caller_supplied():
    """A failed signature authenticates nothing, including the header.

    Any caller can stamp ``kid: bare`` on a token they minted, so presenting it
    as provenance would hand a forger the power to point an operator at a
    healthy shared secret. The line must mark it as untrusted, and must not
    claim it identifies the signer.
    """
    token = mint([user_principal()], key="not-the-shared-secret-but-also-32-bytes+")

    with pytest.raises(PrincipalVerificationError) as exc:
        verify_principal_token(token, CONFIG)

    message = str(exc.value)
    assert "unverified caller-supplied header" in message
    # The trustworthy half is stated first, so the line reads as
    # "here is what we hold" before "here is what they claimed".
    assert message.index("verifier key fp=") < message.index("unverified")


def test_a_forged_kid_cannot_impersonate_the_gateway_in_the_log():
    """The concrete abuse the labelling exists to defuse.

    A forger who never touched our gateway can still present ``kid='bare'``.
    The message must read the same either way — no wording that would tell an
    operator this came from their gateway.
    """
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
        "principals": [user_principal()],
    }
    forged = jwt.encode(
        claims, "an-attacker-key-of-at-least-32-bytes!!", algorithm="HS256",
        headers={"kid": "bare"},
    )

    with pytest.raises(PrincipalVerificationError) as exc:
        verify_principal_token(forged, CONFIG)

    message = str(exc.value)
    assert "unverified caller-supplied header" in message
    for claim in ("our gateway", "signed by", "produced by", "provenance"):
        assert claim not in message.lower(), f"must not assert {claim!r}"


def test_the_diagnostic_never_carries_the_key_or_the_token():
    token = mint([user_principal()], key="not-the-shared-secret-but-also-32-bytes+")

    with pytest.raises(PrincipalVerificationError) as exc:
        verify_principal_token(token, CONFIG)

    assert KEY not in str(exc.value)
    assert token not in str(exc.value)


@pytest.mark.parametrize(
    "reason,token_kwargs",
    [
        ("Signature has expired", {"ttl": -3600}),
        ("Audience doesn't match", {"audience": "baas"}),
        ("Invalid issuer", {"issuer": "somebody-else"}),
    ],
)
def test_each_failure_mode_keeps_its_own_reason(reason: str, token_kwargs: dict):
    """The suffix is added to PyJWT's message, never in place of it.

    Collapsing these into one string would cost the operator the first and
    cheapest split: is this a key problem at all, or a clock/config one?
    """
    with pytest.raises(PrincipalVerificationError) as exc:
        verify_principal_token(mint([user_principal()], **token_kwargs), CONFIG)

    assert reason in str(exc.value)
    assert f"verifier key fp={key_fingerprint(KEY)}" in str(exc.value)


def test_a_token_with_no_readable_header_says_so():
    with pytest.raises(PrincipalVerificationError) as exc:
        verify_principal_token("not-a-jwt-at-all", CONFIG)

    message = str(exc.value)
    assert "unparseable JOSE header" in message
    assert f"verifier key fp={key_fingerprint(KEY)}" in message, (
        "the trustworthy half is present even when the header is not"
    )


def test_a_hostile_kid_cannot_forge_log_lines():
    """The JOSE header is attacker-controlled and reaches a log line.

    A ``kid`` carrying a newline could otherwise append text that reads as a
    separate log entry, so the field is escaped as well as bounded.
    """
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
        "principals": [user_principal()],
    }
    token = jwt.encode(
        claims,
        "a-different-key-that-is-also-32-bytes!!",
        algorithm="HS256",
        headers={"kid": "x\nWARNING - forged log line"},
    )

    with pytest.raises(PrincipalVerificationError) as exc:
        verify_principal_token(token, CONFIG)

    message = str(exc.value)
    assert "\n" not in message, "a newline in kid must not reach the log verbatim"
    assert "\\n" in message, "it is escaped, not silently dropped"


def test_a_long_kid_is_clipped():
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
        "principals": [user_principal()],
    }
    token = jwt.encode(
        claims,
        "a-different-key-that-is-also-32-bytes!!",
        algorithm="HS256",
        headers={"kid": "K" * 500},
    )

    with pytest.raises(PrincipalVerificationError) as exc:
        verify_principal_token(token, CONFIG)

    assert "…" in str(exc.value)
    assert "K" * 100 not in str(exc.value)
