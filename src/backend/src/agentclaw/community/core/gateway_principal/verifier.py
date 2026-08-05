"""Verify the gateway-signed principal token and project it onto our DTOs.

This is the backend half of auth design §7.1. The gateway signs the identity set
it resolved into a short-lived JWT and forwards it as ``X-Avernet-Principal``;
**a component must never trust that header unverified**, so everything here is
about earning the right to believe it:

1. the signature must check out against the shared key,
2. ``aud`` must name *this* component — a token minted for another upstream is
   not replayable here,
3. ``iss`` must be the gateway and ``exp`` must not have passed,
4. the ``principals`` payload must parse onto :mod:`.models`,
5. every principal in the set must agree on one tenant, and that tenant must not
   be the internal one,
6. the set must name an end user — see :func:`_require_user_principal`.

Any failure raises :class:`PrincipalVerificationError`. There is no partial
success and no fallback: a token we cannot fully verify yields no caller, and the
request is answered ``401``.

Transport-agnostic by design (Rule 7) — no framework, no header parsing, no
environment reads, no secret store. The adapter hands it a token string and a
config; that config is assembled in ``utils/gateway_principal_config.py``, which
resolves the signing key through ``SecretResolver``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import jwt
from pydantic import TypeAdapter, ValidationError

from agentclaw.community.core.gateway_principal.errors import (
    PrincipalVerificationError,
)
from agentclaw.community.core.gateway_principal.models import (
    GatewayPrincipal,
    UserPrincipal,
)
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT

# Pinned, not read from the token. Honouring the token's own ``alg`` is the
# classic JWT downgrade: a forged header saying ``none`` (or naming an
# asymmetric algorithm whose "key" is our public material) would verify.
_ALGORITHMS = ("HS256",)

# Clock skew allowance. The gateway's default TTL is 60s, so two containers a few
# seconds apart would otherwise reject live tokens. Fixed, not configurable: a
# knob here is a knob that can be set to hours.
_LEEWAY_SECONDS = 5

# Claims that must be present. ``aud``/``iss`` are also value-checked below;
# requiring them explicitly means a token that simply omits one is rejected
# rather than skipping the check.
_REQUIRED_CLAIMS = ("exp", "iat", "aud", "iss")

_PRINCIPAL_ADAPTER: TypeAdapter[GatewayPrincipal] = TypeAdapter(GatewayPrincipal)

# How much of a JOSE header field reaches a log line. The header is unverified
# attacker-controlled input, so it is clipped before it is ever formatted.
_MAX_HEADER_FIELD = 32

# What a fingerprint of no key at all reads as, so a log line never shows a
# hash of the empty string as though it were a configured key.
_UNSET_FINGERPRINT = "unset"

# RFC 7518 §3.2: an HMAC key for SHA-256 must be at least as long as the hash
# output. PyJWT warns below it, and it is the threshold the fingerprint's
# safety argument rests on — see :func:`key_fingerprint`.
MIN_SIGNING_KEY_BYTES = 32


def is_weak_signing_key(key: str) -> bool:
    """Whether ``key`` is below the strength the shared-secret contract assumes.

    Length is a proxy for entropy, and a coarse one — it cannot tell 32 random
    bytes from 32 repetitions of ``a``. It is still worth checking, because the
    keys that show up short are overwhelmingly the human-chosen ones, and a
    check that catches the common case beats a docstring that assumes the
    problem away.

    Measured in **bytes, not characters**, because bytes are what HMAC
    consumes: a non-ASCII passphrase carries more bytes than it has characters,
    so counting characters would reject a key the algorithm is content with.
    """
    return 0 < len(key.encode("utf-8")) < MIN_SIGNING_KEY_BYTES


def key_fingerprint(key: str) -> str:
    """A short, non-reversible fingerprint of a shared signing key.

    **This algorithm is a cross-component contract.** The gateway computes the
    same fingerprint over its own copy of the key
    (``plugins/principal_signer/bare/_plugin.py``) and both sides log it at
    boot, so that "do the two ends hold the same secret?" is answered by
    comparing two log lines rather than by printing a credential. Change it on
    one side only and the comparison silently becomes meaningless — the golden
    values pinned in both test suites exist to stop that.

    Safe to log **for a key of the mandated strength**, and that qualifier is
    the whole of the argument: SHA-256 is not reversible, so against ≥32 random
    bytes (RFC 7518 §3.2) a truncated digest is no practical brute-force oracle
    for someone who can already read the logs. Against a short or human-chosen
    key it *is* one — 32 bits is enough to confirm a dictionary guess offline,
    and confirming the shared secret means being able to forge identity tokens.

    Nothing in the type system enforces that strength, so
    :func:`is_weak_signing_key` exists and both boot paths warn when a key falls
    below it rather than leaving the precondition asserted-but-unchecked. The
    fingerprint is still emitted for a weak key: suppressing it would remove the
    diagnostic exactly when a deployment is most misconfigured, and the answer
    to a weak shared secret is to replace it, which the warning says outright.

    Safe to log is not safe to *serve* — never put this on an HTTP endpoint,
    where it would let an unauthenticated caller confirm a guessed key.
    """
    if not key:
        return _UNSET_FINGERPRINT
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


@dataclass(frozen=True)
class PrincipalVerifierConfig:
    """Everything needed to verify a forwarded principal token.

    ``signing_key`` is the HMAC secret shared with the gateway's ``bare``
    signer. An empty key means the deployment has not been given one, or could
    not resolve it — see :func:`verify_principal_token`, which then fails every
    verification closed rather than accepting unsigned identity.

    ``audience`` must equal the gateway's upstream-server name for this
    component (``servers:`` in the gateway's ``upstreams.yaml``), and ``issuer``
    its configured ``iss``.
    """

    signing_key: str
    audience: str
    issuer: str

    @property
    def key_fingerprint(self) -> str:
        """This config's key as :func:`key_fingerprint` renders it for a log."""
        return key_fingerprint(self.signing_key)


@dataclass(frozen=True)
class VerifiedCaller:
    """A verified caller: the full identity set the gateway resolved.

    A request can carry more than one identity (a route may require a user and
    optionally accept an app), so the caller *is* a set, not a single principal.
    ``tenant`` and ``user_id`` are the two things this surface scopes by.
    """

    principals: tuple[GatewayPrincipal, ...]

    @property
    def tenant(self) -> str:
        """The tenant every principal in the set agrees on.

        Verification rejects a set that disagrees, so reading the first is
        reading all of them.
        """
        return self.principals[0].tenant

    @property
    def user_id(self) -> str:
        """The owner id to scope data by — a ``user`` principal's subject id.

        Named ``user_id`` because that is the attribute
        ``openapi_v1/principal.py::caller_owner_id`` looks for — it was written
        against "whatever shape the auth workstream's verified principal takes",
        so satisfying it needs no change to any handler.

        A ``user`` principal is the only source, and
        :func:`_require_user_principal` admits no set that lacks one carrying a
        usable id — so a caller reaching this property always has an owner. The
        two facts are the same decision seen from two sides: this surface scopes
        by an owner, so it serves only callers that name one.

        Both read the set through :func:`_first_user_principal`, so the
        admission check and this property cannot disagree about *which*
        principal supplies the owner.

        The ``""`` fallback is unreachable through
        :func:`verify_principal_token`. It stays so that a hand-constructed
        instance — a test fixture, say — degrades to "no owner", which
        ``caller_owner_id`` turns into a ``401``, rather than raising something
        no caller-facing code is written to catch.
        """
        principal = _first_user_principal(self.principals)
        return principal.subject.id if principal is not None else ""


def verify_principal_token(
    token: str, config: PrincipalVerifierConfig
) -> VerifiedCaller:
    """Verify ``token`` and return the caller it carries.

    Raises :class:`PrincipalVerificationError` on any failure — bad signature,
    wrong audience, expired, unparseable payload, contradictory tenants, an
    identity set that names no end user, or no signing key configured at all.
    """
    if not config.signing_key:
        # No key means we cannot tell a gateway token from a forged one. The
        # only safe reading of "unconfigured" is "trust nothing".
        raise PrincipalVerificationError("no principal signing key is configured")
    if not token:
        raise PrincipalVerificationError("empty principal token")

    try:
        claims = jwt.decode(
            token,
            config.signing_key,
            algorithms=list(_ALGORITHMS),
            audience=config.audience,
            issuer=config.issuer,
            leeway=_LEEWAY_SECONDS,
            options={"require": list(_REQUIRED_CLAIMS)},
        )
    except jwt.PyJWTError as exc:
        # PyJWT's own message already separates the failure modes ("Signature
        # verification failed" vs "Signature has expired" vs "Invalid
        # audience"), so it is kept verbatim. What it cannot say is *which key
        # and which contract* we judged the token against — and that is what
        # turns an unattributable rejection into a diagnosis.
        #
        # The two halves of the suffix have very different standing, and the
        # wording keeps them apart. Our key fingerprint, audience and issuer
        # come from this process's own configuration: trustworthy, and the
        # thing to reason from. The JOSE header comes from the request and is
        # unauthenticated — a forger sets ``kid`` to whatever makes their token
        # look legitimate — so it is labelled as caller-supplied rather than
        # presented as provenance. Reading it as proof would mean rotating a
        # healthy secret the moment someone starts forging tokens.
        #
        # None of this reaches the caller: every failure here funnels into one
        # fixed ``401 Unauthorized`` (``openapi_v1/responses.py``), so naming
        # the mismatch for the operator does not tell a forger what to fix.
        raise PrincipalVerificationError(
            f"principal token rejected: {exc} "
            f"[verifier key fp={config.key_fingerprint}, "
            f"expects aud={config.audience!r} iss={config.issuer!r}; "
            f"{_unverified_token_header(token)}]"
        ) from exc

    principals = _parse_principals(claims.get("principals"))
    _reject_contradictory_tenant(principals)
    _require_user_principal(principals)
    return VerifiedCaller(principals=principals)


def _clip(value: object) -> str:
    """Render an unverified header field for a log line, bounded and escaped.

    ``repr`` rather than the bare string: it escapes newlines and control
    characters, so a crafted ``kid`` cannot forge extra log lines around itself.
    """
    text = str(value)
    if len(text) > _MAX_HEADER_FIELD:
        text = text[:_MAX_HEADER_FIELD] + "…"
    return repr(text)


def _unverified_token_header(token: str) -> str:
    """Render the token's JOSE header for a log line, marked as unverified.

    **This is a hint, never evidence of provenance, and the log line says so.**
    A failed signature means nothing about the token has been authenticated —
    including its header — so anyone able to reach this component can put
    ``alg: HS256, kid: bare`` on a token they minted themselves. Reading
    ``kid=bare`` as "our gateway with the wrong key" is therefore exactly the
    inference a forger gets for free, and during forged-token traffic it would
    send an operator to rotate a shared secret that was never broken.

    What it is good for is the cheap negative and the corroborating detail: an
    unexpected ``alg``, an unfamiliar ``kid``, a header that will not parse at
    all. Those say "look somewhere other than the shared key" and cost nothing
    to carry.

    **The authoritative signal is the pair of boot fingerprints** — those come
    from each component's own resolved configuration, not from the request, so
    they cannot be influenced by a caller. Any diagnosis that matters should
    rest on them; see the runbook in ``docs/openapi-v1/README.md``.

    Used for nothing but this string: no decision reads it, the verifier's
    ``alg`` stays pinned to :data:`_ALGORITHMS`, and :func:`_clip` bounds and
    escapes it on the way out. Only the header is read — never the payload,
    whose claims are the unverified assertions this function exists to distrust.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        return "unparseable JOSE header"
    return (
        "unverified caller-supplied header "
        f"alg={_clip(header.get('alg'))} kid={_clip(header.get('kid'))}"
    )


def _parse_principals(raw: object) -> tuple[GatewayPrincipal, ...]:
    """Project the ``principals`` claim onto our DTOs, or fail closed."""
    if not isinstance(raw, list):
        raise PrincipalVerificationError(
            "principal token carries no 'principals' list"
        )
    if not raw:
        # The gateway adds no header for an empty identity set, so an empty list
        # is a malformed token rather than an anonymous caller.
        raise PrincipalVerificationError("principal token carries no identities")
    try:
        return tuple(_PRINCIPAL_ADAPTER.validate_python(item) for item in raw)
    except ValidationError as exc:
        raise PrincipalVerificationError(
            f"principal payload does not match the expected contract: {exc}"
        ) from exc


def _reject_contradictory_tenant(principals: tuple[GatewayPrincipal, ...]) -> None:
    """Require exactly one non-internal tenant across the whole identity set."""
    tenants = {principal.tenant for principal in principals}
    if len(tenants) > 1:
        # One request cannot belong to two tenants. Picking one would be
        # inventing an answer the gateway did not give us.
        raise PrincipalVerificationError(
            f"principal token mixes {len(tenants)} tenants"
        )
    tenant = next(iter(tenants))
    if not tenant:
        raise PrincipalVerificationError("principal token carries an empty tenant")
    if tenant == DEFAULT_AVERNET_TENANT:
        # ``teamclaw`` owns every pre-existing internal row. Accepting it off the
        # wire would hand an external caller the internal tenant's data — the
        # exact failure tenant isolation exists to prevent. No gateway tenant is
        # named this today; if an internal-through-gateway path is ever designed,
        # lift this guard deliberately, with that design written down.
        raise PrincipalVerificationError(
            "principal token names the internal tenant, which is not routable "
            "from the public surface"
        )


def _first_user_principal(
    principals: tuple[GatewayPrincipal, ...],
) -> UserPrincipal | None:
    """The user principal an owner is derived from, or ``None`` when there is none.

    ``None`` is a real state of the contract here — "this identity set names no
    end user" is exactly what :func:`_require_user_principal` exists to detect —
    so the optional return is the state, not a widened type.

    One function rather than two loops so the admission check and
    :attr:`VerifiedCaller.user_id` cannot disagree about *which* principal
    supplies the owner. That matters for a set carrying two user principals: the
    gateway never produces one (it resolves at most a single identity per type),
    but the ``principals`` claim is a list, so a token can present two. Checking
    "some user has a usable id" while deriving the owner from "the first user"
    would admit a set whose first user has a blank id and then scope by nothing.
    """
    for principal in principals:
        if isinstance(principal, UserPrincipal):
            return principal
    return None


def _require_user_principal(principals: tuple[GatewayPrincipal, ...]) -> None:
    """Refuse an identity set that names no end user.

    The public surface scopes every read and write to an owner, and a ``user``
    principal is the only identity that names one. ``app.owners`` is free-text
    "developer/org" attribution (``varchar(1024)``, plural), and the gateway's
    access-key registry has no owner column at all — so neither identifies a
    person, and guessing one is a cross-account data bug. A ``bot`` principal
    does carry ``owner_id``, but a bot calling on its own behalf is not a caller
    this API is defined for; admitting it would silently let a bot act as its
    owner across the whole public contract.

    Enforced **here** rather than at each handler's owner lookup, and that is the
    point. Refusing in ``caller_owner_id`` only refuses handlers that call it —
    the four in ``openapi_v1/resources/router.py`` that scope on a caller-supplied
    ``bot_id`` never do, so an unscopeable caller reached them. A rule every
    handler has to remember is not a rule. Refusing during verification makes it
    hold for every route that exists and every route added later.

    Sets carrying *extra* identities alongside the user are accepted, not
    refused: the gateway forwards the whole set it resolved, so a route declaring
    ``user: required, app: optional`` legitimately yields two principals.
    Rejecting a set that merely *contains* a non-user identity would refuse a
    request the gateway considers valid. The user is required; the rest are
    ignored.

    This is the narrow reading of an open question, not an answer to it. What an
    ``app`` or ``access_key`` caller should own is still unsettled (auth design
    §14 Q4); delegation — a partner acting for a verified end user, which *does*
    name a person — is the designed way to widen this (§15). Lift the guard
    there, deliberately.
    """
    user = _first_user_principal(principals)
    if user is None:
        # The types are named for the operator reading the log, never for the
        # caller: ``require_principal`` answers one fixed 401 for every
        # verification failure, so a caller cannot tell a refused identity type
        # from a bad signature.
        carried = ", ".join(sorted({principal.type.value for principal in principals}))
        raise PrincipalVerificationError(
            f"principal token names no user identity (carries: {carried}); the "
            "public surface admits only callers that name an end user"
        )
    # A ``user`` principal with a blank subject id names an end user no better
    # than an access key does — and the type check alone would admit it, because
    # both sides model the id as an unconstrained ``str``. It is reachable: the
    # gateway's google strategy reads ``body["sub"]``, which raises on a
    # *missing* claim but passes an empty one straight through.
    #
    # Whitespace is stripped for the test rather than merely checked falsy (the
    # empty-tenant guard above can be laxer because a tenant is compared, not
    # used as a key): an id of ``"   "`` would be carried into owner-scoped
    # queries as a real value.
    if not user.subject.id.strip():
        raise PrincipalVerificationError(
            "principal token carries a user identity with a blank subject id, "
            "which names no owner to scope by"
        )
