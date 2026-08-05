"""BarePrincipalSigner — single-box HMAC (HS256) signer for the community edition.

Signs the resolved identity set as a short-lived JWT with ``iss/aud/iat/exp`` +
a ``principals`` claim, keyed by a shared HMAC secret (kid in the JOSE header
for rotation). NOT production-grade — sofa uses asymmetric signing + KMS
(auth design §7.1).

The signing key is resolved via the :class:`SecretResolver` SPI (community
flavor reads it from the environment); ``kid`` / ``issuer`` / ``ttl_seconds``
are non-secret and come from ``user_config.principal_signer``.

**There is no fallback key.** An unresolvable secret used to mean "sign with a
committed constant", which was worse than useless in both directions: a
committed shared secret is a committed credential, and no peer would have
accepted those tokens anyway — the backend ships no fallback, so it never holds
the constant this side would have signed with. The only thing the fallback
bought was the *appearance* of a working gateway, paid for with a signature
failure on another host that named neither this component nor this cause.

So a missing key now fails closed, mirroring the backend's contract exactly:

- in a strict profile the process refuses to boot — see ``strict`` on
  :func:`load_signer_config`, and ``_STRICT_ENVS`` in
  ``bootstrap/_principal_signer.py`` for which ``SERVER_ENV`` spellings those
  are (``prepub`` and ``gray`` are aliases the backend folds into ``pre`` and
  ``prod``, so both sides must honour them or the contract splits). A gateway
  that cannot sign is broken, not degraded, and the rollout should fail rather
  than a ticket land later.
- everywhere else it boots with an empty key and :meth:`BarePrincipalSigner.sign`
  raises on every attempt. The forwarder already answers ``500 principal
  signing failed`` and logs the traceback, so the failure names the component
  that is actually misconfigured.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import jwt

from gateway.community.config import PrincipalSignerPluginConfig
from gateway.community.logger import get_logger
from gateway.community.spi.authn import Principal, PrincipalType
from gateway.community.spi.secret_resolver import SecretResolver

# What a fingerprint of no key at all reads as. Mirrors the backend so the two
# log lines are comparable even in the degenerate case.
_UNSET_FINGERPRINT = "unset"

# RFC 7518 §3.2: an HMAC key for SHA-256 must be at least as long as the hash
# output. Mirrors the backend's constant of the same name — this is one shared
# secret, so both ends judge its strength by the same rule.
MIN_SIGNING_KEY_BYTES = 32


def is_weak_signing_key(key: str) -> bool:
    """Whether ``key`` is below the strength the shared-secret contract assumes.

    Length is a coarse proxy for entropy — it cannot tell 32 random bytes from
    32 repetitions of ``a`` — but the keys that turn up short are overwhelmingly
    the human-chosen ones, so it catches the case that matters. Measured in
    bytes, not characters, because that is what HMAC consumes — a non-ASCII
    passphrase carries more bytes than it has characters, and counting
    characters would reject a key the algorithm is content with.
    """
    return 0 < len(key.encode("utf-8")) < MIN_SIGNING_KEY_BYTES


class PrincipalSigningKeyMissingError(RuntimeError):
    """Raised when a signature is asked for and no signing key was resolved.

    A distinct type so the boot-time refusal and the request-time refusal are
    the same fact told twice, and so a caller can tell "misconfigured" from any
    other signing failure.
    """


def key_fingerprint(key: str) -> str:
    """A short, non-reversible fingerprint of the shared signing key.

    **This algorithm is a cross-component contract**, duplicated rather than
    shared because the gateway and the backend are separate distributions with
    no common package. The backend computes the identical value in
    ``agentclaw.community.core.gateway_principal.verifier.key_fingerprint`` and
    logs it at boot; comparing the two log lines is how an operator answers "do
    both ends hold the same secret?" without either side printing a credential.

    The duplication is the risk, so both suites pin the same golden values for
    the same inputs — change the algorithm here alone and those tests fail
    rather than the comparison quietly becoming meaningless.

    Safe to log **for a key of the mandated strength**, and the qualifier is the
    argument: SHA-256 is not reversible, so against ≥32 random bytes a truncated
    digest is no practical brute-force oracle for someone who can already read
    the logs. Against a short or human-chosen key it *is* one, and confirming
    the shared secret means being able to forge identity tokens. Nothing
    enforces that strength, so :func:`is_weak_signing_key` gates a boot warning
    rather than the precondition being merely asserted.

    Safe to log is not safe to *serve* — this must never appear on an HTTP
    endpoint, where it would let an unauthenticated caller confirm a guessed key.
    """
    if not key:
        return _UNSET_FINGERPRINT
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


@dataclass(frozen=True)
class PrincipalSignerConfig:
    """Runtime config for :class:`BarePrincipalSigner`."""

    signing_key: str
    kid: str = "bare"
    issuer: str = "gateway"
    ttl_seconds: int = 60

    @property
    def key_fingerprint(self) -> str:
        """This config's key as :func:`key_fingerprint` renders it for a log."""
        return key_fingerprint(self.signing_key)


class BarePrincipalSigner:
    """HMAC HS256 signer — the bare flavor of the :class:`PrincipalSigner` SPI."""

    def __init__(
        self,
        config: PrincipalSignerConfig,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._cfg = config
        self._clock = clock

    async def sign(
        self, principals: Mapping[PrincipalType, Principal], *, audience: str
    ) -> str:
        now = int(self._clock())
        claims = {
            "iss": self._cfg.issuer,
            "aud": audience,
            "iat": now,
            "exp": now + self._cfg.ttl_seconds,
            "principals": [p.model_dump(mode="json") for p in principals.values()],
        }
        return await self.sign_token(claims)

    async def sign_token(self, claims: Mapping[str, object]) -> str:
        if not self._cfg.signing_key:
            # Refusing beats signing with nothing. PyJWT will happily HMAC with
            # an empty key, and the result is a token whose "signature" anyone
            # can reproduce — an unauthenticated caller could then mint any
            # identity for any upstream that made the same mistake. The
            # forwarder turns this into ``500 principal signing failed`` with a
            # traceback, so the error names this component rather than
            # surfacing as an unattributable 401 one hop away.
            raise PrincipalSigningKeyMissingError(
                "no principal signing key is configured, so this gateway "
                "cannot assert any identity to an upstream. Provision the "
                "secret named by user_config.principal_signer.secret_name."
            )
        return jwt.encode(
            claims,
            self._cfg.signing_key,
            algorithm="HS256",
            headers={"kid": self._cfg.kid},
        )


def _resolve_signing_key(
    signer_cfg: PrincipalSignerPluginConfig,
    secret_resolver: SecretResolver,
) -> str:
    """Resolve the HMAC signing key via the SecretResolver, or return ``""``.

    ``""`` means this deployment has no key. There is no stand-in: see the
    module docstring for why the previous dev fallback was removed rather than
    made louder. sofa (asymmetric + KMS) resolves a real key through the same
    SecretResolver seam.

    The resolved value is **stripped**, matching what the backend does to its
    copy (``utils/gateway_principal_config.py``). Without that the two sides
    disagree over any secret provisioned with a trailing newline — each looks
    correctly configured, every token fails its signature check, and nothing
    on either side says why.
    """
    log = get_logger("principal_signer")
    material = secret_resolver.get_secret(signer_cfg.secret_name)
    raw = getattr(material, "secret_value", "") or ""
    key = raw.strip()
    if not key:
        # Says what will happen, not just what was not found: the consequence
        # of an unresolved key lands on every subsequent request, and the
        # operator reading this line is the one who can prevent that.
        log.warning(
            "Principal signing key not found via SecretResolver "
            "(secret_name=%r) — this gateway cannot sign any identity, so "
            "every forwarded request will fail with 'principal signing "
            "failed'. Provision the secret's value in this environment.",
            signer_cfg.secret_name,
        )
        return ""

    if key != raw:
        log.warning(
            "Principal signing key (secret_name=%r) carries %d character(s) "
            "of surrounding whitespace, which is stripped before use "
            "(fp untrimmed=%s, trimmed=%s). Provision the value without it so "
            "both ends of the contract hash the same bytes.",
            signer_cfg.secret_name,
            len(raw) - len(key),
            key_fingerprint(raw),
            key_fingerprint(key),
        )
    return key


def load_signer_config(
    signer_cfg: PrincipalSignerPluginConfig,
    secret_resolver: SecretResolver,
    *,
    strict: bool,
) -> PrincipalSignerConfig:
    """Build :class:`PrincipalSignerConfig` from typed config + SecretResolver.

    Non-secret fields (``kid`` / ``issuer`` / ``ttl_seconds``) come from the
    ``user_config.principal_signer`` block; the signing key is resolved via
    ``secret_resolver`` using ``signer_cfg.secret_name``.

    ``strict`` decides what an unresolvable key means, and mirrors the same
    argument on the backend's ``init_principal_verifier_config`` — one contract,
    one rule, told the same way on both sides. ``True`` (``pre``/``prod``)
    raises, because a gateway that cannot assert an identity serves nothing and
    should fail its rollout rather than every request. ``False`` boots with an
    empty key for the environments that legitimately have none, where each
    attempt to sign then refuses individually.

    Raises:
        PrincipalSigningKeyMissingError: when ``strict`` and no key resolved.
    """
    signing_key = _resolve_signing_key(signer_cfg, secret_resolver)
    if not signing_key and strict:
        raise PrincipalSigningKeyMissingError(
            "no principal signing key resolved: "
            f"user_config.principal_signer.secret_name={signer_cfg.secret_name!r} "
            "produced no value. This gateway could not assert any identity to "
            "an upstream, so this environment refuses to boot. Provision the "
            "secret's value in this profile's secret store."
        )

    config = PrincipalSignerConfig(
        signing_key=signing_key,
        kid=signer_cfg.kid,
        issuer=signer_cfg.issuer,
        ttl_seconds=signer_cfg.ttl_seconds,
    )
    # The counterpart to the backend's boot line. Signing succeeds regardless of
    # *which* key is held, so this side observes nothing when the two disagree —
    # only the upstream does, as a signature failure it cannot attribute.
    # Emitting the fingerprint here is what makes the two halves comparable at
    # all, and ``iss`` rides along because it is configurable on this side and
    # hardcoded on the other: change it here alone and every request 401s.
    get_logger("principal_signer").info(
        "principal signer configured (secret=%r, key fp=%s, key len=%d, "
        "kid=%r, iss=%r, ttl=%ds)",
        signer_cfg.secret_name,
        config.key_fingerprint,
        len(config.signing_key),
        config.kid,
        config.issuer,
        config.ttl_seconds,
    )
    if is_weak_signing_key(config.signing_key):
        # The line above publishes a fingerprint of this key, which is only
        # safe while the key is strong — against a guessable one a truncated
        # digest confirms a dictionary guess offline. Warn rather than withhold
        # the fingerprint: the diagnostic matters most on a misconfigured
        # deployment, and the real remedy is a better secret.
        get_logger("principal_signer").warning(
            "the principal signing key is %d bytes, below the %d-byte minimum "
            "for HMAC-SHA256 (RFC 7518 §3.2). Replace it with at least %d "
            "random bytes: a guessable shared secret can be recovered from the "
            "fingerprint logged above, and whoever recovers it can forge any "
            "caller identity to every upstream.",
            len(config.signing_key.encode("utf-8")),
            MIN_SIGNING_KEY_BYTES,
            MIN_SIGNING_KEY_BYTES,
        )
    return config
