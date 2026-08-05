"""BarePrincipalSigner — single-box HMAC (HS256) signer for the community edition.

Signs the resolved identity set as a short-lived JWT with ``iss/aud/iat/exp`` +
a ``principals`` claim, keyed by a shared HMAC secret (kid in the JOSE header
for rotation). NOT production-grade — sofa uses asymmetric signing + KMS
(auth design §7.1).

The signing key is resolved via the :class:`SecretResolver` SPI (community
flavor reads it from the environment); ``kid`` / ``issuer`` / ``ttl_seconds``
are non-secret and come from ``user_config.principal_signer``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import jwt

from gateway.community.config import PrincipalSignerPluginConfig
from gateway.community.spi.authn import Principal, PrincipalType
from gateway.community.spi.secret_resolver import SecretResolver

_DEV_FALLBACK_KEY = "avernet-dev-signing-key-NOT-FOR-PROD"


@dataclass(frozen=True)
class PrincipalSignerConfig:
    """Runtime config for :class:`BarePrincipalSigner`."""

    signing_key: str
    kid: str = "bare"
    issuer: str = "gateway"
    ttl_seconds: int = 60


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
    """Resolve the HMAC signing key via the SecretResolver, with a dev fallback.

    A missing secret (resolver returns ``None`` or an empty value) falls back to
    a fixed dev secret and logs a warning — fine for single-box/tests, NOT for
    production. sofa (asymmetric + KMS) resolves a real key through the same
    SecretResolver seam.
    """
    material = secret_resolver.get_secret(signer_cfg.secret_name)
    key = getattr(material, "secret_value", "") or ""
    if not key:
        from gateway.community.logger import get_logger

        get_logger("principal_signer").warning(
            "Principal signing key not found via SecretResolver (secret_name=%r) "
            "— using dev fallback key (NOT for production).",
            signer_cfg.secret_name,
        )
        key = _DEV_FALLBACK_KEY
    return key


def load_signer_config(
    signer_cfg: PrincipalSignerPluginConfig,
    secret_resolver: SecretResolver,
) -> PrincipalSignerConfig:
    """Build :class:`PrincipalSignerConfig` from typed config + SecretResolver.

    Non-secret fields (``kid`` / ``issuer`` / ``ttl_seconds``) come from the
    ``user_config.principal_signer`` block; the signing key is resolved via
    ``secret_resolver`` using ``signer_cfg.secret_name``.
    """
    return PrincipalSignerConfig(
        signing_key=_resolve_signing_key(signer_cfg, secret_resolver),
        kid=signer_cfg.kid,
        issuer=signer_cfg.issuer,
        ttl_seconds=signer_cfg.ttl_seconds,
    )
