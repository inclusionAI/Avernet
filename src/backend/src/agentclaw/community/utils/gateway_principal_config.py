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
  via ``CommunitySecretResolver``; or ``gateway_principal.signing_key`` in
  ``application-singlebox.yaml`` via ``LocalSecretResolver``.

  **In ``pre``/``prod`` an unresolvable key fails the boot** — see ``strict`` on
  :func:`init_principal_verifier_config`. Serving the public API without one is
  not a degraded mode, it is a broken deployment that answers 401 to everything
  while looking healthy, so it is caught at rollout rather than in a ticket.

  Everywhere else — singlebox, local, dev, tests — **anything short of a real key
  denies every public request** rather than stopping the boot: no name
  registered, no such secret, an empty value, or a resolver that raises. Those
  environments legitimately have no key, and that is the pre-auth state this
  replaces. We ship **no fallback key** either way, because a committed shared
  secret is a committed credential. Use at least 32 bytes — RFC 7518 §3.2 for
  SHA-256, and PyJWT warns below it.

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

from agentclaw.community.core.gateway_principal import PrincipalVerifierConfig
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
    legitimately have no key: singlebox ships ``gateway_principal.signing_key``
    empty on purpose (a committed shared secret is a committed credential), and
    local/dev boots run without a gateway at all. Failing those closed keeps
    them bootable while still refusing every unverifiable request.

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
        logger.info("gateway principal verification is configured")
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
    """Return the shared key, or ``""`` when this deployment has none."""
    if not secret_name:
        logger.warning(
            "no 'gateway_principal_signing_key' registered in the secret_names "
            "config — the public API will answer 401"
        )
        return ""

    try:
        secret = resolver.get_secret(secret_name)
    except Exception:
        # A secret-store outage must not crash boot. Deny instead: without the
        # key we cannot tell a gateway token from a forged one, which is the
        # same answer as never having had one.
        logger.exception(
            "resolving the principal signing key failed — the public API will "
            "answer 401"
        )
        return ""

    if secret is None:
        logger.warning(
            "secret %r is not present in the secret store — the public API will "
            "answer 401",
            secret_name,
        )
        return ""

    key = str(getattr(secret, "secret_value", "") or "").strip()
    if not key:
        logger.warning(
            "secret %r resolved with an empty value — the public API will "
            "answer 401",
            secret_name,
        )
    return key


def reset_principal_verifier_config_cache() -> None:
    """Drop the installed config so a test can re-initialize.

    Production never calls this — the config is fixed for a process lifetime.
    """
    global _config
    _config = _DENY
