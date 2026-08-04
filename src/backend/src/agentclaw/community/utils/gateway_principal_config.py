"""Config for gateway principal verification.

The consumer is ``resolve_avernet_tenant``, which ``AvernetTenantMiddleware``
calls from the raw ASGI layer *before* any route and therefore outside the
injector — so this config cannot arrive as an ``Injected(...)`` parameter. It is
**pushed in at boot instead of pulled at request time**: the composition root
(``adapters/http/app.py``) resolves the key once and calls
:func:`init_principal_verifier_config`, which is the "boot-time callbacks capture
their deps at registration time" mechanism, not the forbidden service locator
(see ``tests/community/architecture/test_no_service_locator_calls.py``). Nothing
here imports ``di`` — the caller passes the resolver and the name in, so this
module keeps no reverse dependency on the composition root.

What comes from where:

- **The signing key** — the HMAC secret shared with the gateway — is resolved
  through :class:`SecretResolver` under
  ``SecretNamesConfig.gateway_principal_signing_key``. That name **defaults**,
  so a deployment configures only the *value*, wherever its resolver reads that
  from: the corp secret store (corp env overlays also override the name with the
  real registry key); ``AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE``
  via ``CommunitySecretResolver``. Singlebox resolves **nothing** — it has no
  secret store and ships no local stand-in, so the public surface denies there.

  **In ``pre``/``prod`` an unresolvable key fails the boot** — see ``strict`` on
  :func:`init_principal_verifier_config`. Serving the public API without one is
  not a degraded mode, it is a broken deployment that answers 401 to everything
  while looking healthy, so it is caught at rollout rather than in a ticket.

  Everywhere else — singlebox, local, dev, tests — **anything short of a real key
  denies every public request** rather than stopping the boot: no name
  registered, no such secret, an empty value, or a resolver that raises. Those
  environments legitimately have no key, and that is the pre-auth state this
  replaces. We ship **no fallback key** either way, because a committed shared
  secret is a committed credential — and since 2026-08-04 the gateway ships none
  either, so both ends of this contract fail closed by the same rule. Use at
  least 32 bytes — RFC 7518 §3.2 for SHA-256, and PyJWT warns below it.

- **``aud`` and ``iss``** are fixed in code, not configuration — a deliberate
  call, so that one wire contract has one spelling rather than a knob per side.

  ``aud`` is not configurable on the signing side at all: the gateway signs it
  from the upstream server's own name (``servers:`` in its
  ``configs/application.yaml``), so a knob here could not change the contract,
  only break it.

  ``iss`` **is** configurable on the gateway (``user_config.principal_signer.
  issuer``, since gateway #673 moved that config into the file). Its default is
  ``gateway``, which is what :data:`_ISSUER` matches, so the contract holds as
  shipped. But the coupling is now real and unenforced: **changing the gateway's
  ``issuer`` requires changing this constant in the same release**, or every
  ``/openapi/v1`` request answers 401. If that ever needs to vary per
  deployment, this is the line to revisit.

Resolved once, at boot: the key is deployment configuration, and a per-request
secret-store round trip on the hot path buys nothing. There is deliberately no
re-read, so rotating the shared secret requires a restart on both sides.
"""

from __future__ import annotations

from agentclaw.community.core.gateway_principal import (
    MIN_SIGNING_KEY_BYTES,
    PrincipalVerifierConfig,
    is_weak_signing_key,
    key_fingerprint,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.secret_resolver import SecretResolver

logger = get_logger()

# The ``aud`` we accept: this component's name under ``servers:`` in the
# gateway's ``configs/application.yaml``, which is what its forwarder signs the
# token's audience as (``adapters/web/_forward.py``). A token minted for another
# upstream must not verify here, so these two spellings have to match.
_AUDIENCE = "backend"

# The ``iss`` we accept. Matches the default of the gateway's
# ``user_config.principal_signer.issuer``, which is configurable on that side —
# so this constant and that setting must move together or every request 401s.
_ISSUER = "gateway"

# What every unresolved deployment gets: an empty key, which the verifier treats
# as "trust nothing" and answers 401 to everything. Also the pre-boot value, so
# a request that somehow arrives before init denies rather than crashes.
_DENY = PrincipalVerifierConfig(signing_key="", audience=_AUDIENCE, issuer=_ISSUER)

_config: PrincipalVerifierConfig = _DENY


def init_principal_verifier_config(
    resolver: SecretResolver, secret_name: str, *, strict: bool
) -> PrincipalVerifierConfig:
    """Resolve the shared signing key and install it process-wide.

    Called once from the composition root, which owns all three arguments:
    ``injector.get(SecretResolver)``,
    ``injector.get(SecretNamesConfig).gateway_principal_signing_key``, and
    whether this environment must have a key.

    ``strict`` decides what an unresolvable key means. A deployment that serves
    the public API and cannot verify a principal is broken, not degraded: it
    would answer 401 to every ``/openapi/v1`` request while looking healthy, and
    the misconfiguration would surface as a support ticket rather than a failed
    rollout. So ``strict=True`` raises and the process refuses to boot.

    ``strict=False`` installs the deny config instead, for the environments that
    legitimately have no key: singlebox ships no local key at all (a committed
    shared secret is a committed credential), and local/dev boots run without a
    gateway. Failing those closed keeps them bootable while still refusing every
    unverifiable request.

    Raises:
        RuntimeError: when ``strict`` and no usable key could be resolved.
    """
    global _config
    signing_key = _resolve_signing_key(resolver, secret_name)
    if not signing_key and strict:
        raise RuntimeError(
            "gateway principal verification has no signing key: "
            f"secret_names.gateway_principal_signing_key={secret_name!r} "
            "resolved to nothing. The public /openapi/v1 surface would answer "
            "401 to every request, so this environment refuses to boot. "
            "Register the name and provision its value in this profile's "
            "secret store."
        )

    _config = PrincipalVerifierConfig(
        signing_key=signing_key, audience=_AUDIENCE, issuer=_ISSUER
    )
    if signing_key:
        # The fingerprint is the whole point of this line. The two halves of
        # this contract never meet at request time — the gateway signs
        # successfully every time, and only this component ever observes a
        # mismatch — so boot is the one place their keys can be compared at
        # all. Emitting a non-reversible fingerprint on both sides turns "do we
        # hold the same secret?" into a diff of two log lines, instead of a
        # ``Signature verification failed`` with no way to tell a wrong secret
        # from a stale one. ``len`` rides along because two keys that differ
        # only by stray whitespace hash differently but look identical in a
        # secret store.
        logger.info(
            "gateway principal verification is configured "
            "(secret=%r, key fp=%s, key len=%d, aud=%r, iss=%r)",
            secret_name,
            _config.key_fingerprint,
            len(signing_key),
            _AUDIENCE,
            _ISSUER,
        )
        if is_weak_signing_key(signing_key):
            # The line above publishes a fingerprint of this key, and that is
            # only safe while the key is strong — against a guessable one a
            # truncated digest confirms a dictionary guess offline. Warn rather
            # than withhold the fingerprint: the diagnostic matters most on a
            # misconfigured deployment, and the real remedy is a better secret.
            logger.warning(
                "the gateway principal signing key is %d bytes, below the "
                "%d-byte minimum for HMAC-SHA256 (RFC 7518 §3.2). Replace it "
                "with at least %d random bytes: a guessable shared secret can "
                "be recovered from the fingerprint logged above, and whoever "
                "recovers it can forge any caller identity",
                len(signing_key.encode("utf-8")),
                MIN_SIGNING_KEY_BYTES,
                MIN_SIGNING_KEY_BYTES,
            )
    else:
        logger.warning(
            "gateway principal verification has no signing key — every "
            "/openapi/v1 request will answer 401"
        )
    return _config


def get_principal_verifier_config() -> PrincipalVerifierConfig:
    """Return the process-wide verifier config.

    Before :func:`init_principal_verifier_config` runs — and after it runs
    without finding a key — this is the deny config.
    """
    return _config


def _resolve_signing_key(resolver: SecretResolver, secret_name: str) -> str:
    """Return the shared key, or ``""`` when this deployment has none.

    Logs *what* failed, never *what happens next*: the caller decides that from
    ``strict``, and predicting "will answer 401" here would be wrong in the
    strict case, where the process raises and never serves a request at all.
    """
    if not secret_name:
        logger.warning(
            "no 'gateway_principal_signing_key' registered in the secret_names "
            "config — no principal signing key resolved"
        )
        return ""

    try:
        secret = resolver.get_secret(secret_name)
    except Exception:
        # A secret-store outage must not crash boot. Deny instead: without the
        # key we cannot tell a gateway token from a forged one, which is the
        # same answer as never having had one.
        logger.exception(
            "resolving the principal signing key failed — no principal "
            "signing key resolved"
        )
        return ""

    if secret is None:
        logger.warning(
            "secret %r is not present in the secret store — no principal "
            "signing key resolved",
            secret_name,
        )
        return ""

    raw = str(getattr(secret, "secret_value", "") or "")
    key = raw.strip()
    if not key:
        logger.warning(
            "secret %r resolved with an empty value — no principal signing "
            "key resolved",
            secret_name,
        )
        return key

    if key != raw:
        # Stripping is silent otherwise, and silence is what makes this bite: a
        # trailing newline (routine when a secret is injected from a file) is
        # invisible in a secret store, and the two ends then hash different
        # bytes from what looks like one value. The gateway strips too, so the
        # contract still holds — but a gateway released before it did signs
        # with the untrimmed bytes, and this line names the fingerprint that
        # such a peer would report, so a mixed-version rollout is one grep
        # rather than a second incident.
        logger.warning(
            "secret %r carries %d character(s) of surrounding whitespace, "
            "which this side strips before use (fp untrimmed=%s, trimmed=%s). "
            "Provision the value without it so both ends of the contract hash "
            "the same bytes",
            secret_name,
            len(raw) - len(key),
            key_fingerprint(raw),
            key_fingerprint(key),
        )
    return key


def reset_principal_verifier_config_cache() -> None:
    """Drop the installed config so a test can re-initialize.

    Production never calls this — the config is fixed for a process lifetime.
    """
    global _config
    _config = _DENY
