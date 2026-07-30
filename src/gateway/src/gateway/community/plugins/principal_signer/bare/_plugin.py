"""BarePrincipalSigner — single-box HMAC (HS256) signer for the community edition.

Signs the resolved identity set as a short-lived JWT with ``iss/aud/iat/exp`` +
a ``principals`` claim, keyed by a shared HMAC secret (kid in the JOSE header
for rotation). NOT production-grade — sofa uses asymmetric signing + KMS
(auth design §7.1).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import jwt

from gateway.community.spi.authn import Principal, PrincipalType

_KID_ENV = "AVERNET_PRINCIPAL_SIGNING_KID"
_KEY_ENV = "AVERNET_PRINCIPAL_SIGNING_KEY"
_TTL_ENV = "AVERNET_PRINCIPAL_SIGNING_TTL"
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


def load_signer_config(
    env: Mapping[str, str] | None = None,
) -> PrincipalSignerConfig:
    """Build :class:`PrincipalSignerConfig` from env, with a dev fallback key.

    A missing ``AVERNET_PRINCIPAL_SIGNING_KEY`` falls back to a fixed dev secret
    and logs a warning — fine for single-box/tests, NOT for production. sofa
    (asymmetric + KMS) is a separate workstream and will require a real key.
    """
    env = os.environ if env is None else env
    key = env.get(_KEY_ENV, "")
    kid = env.get(_KID_ENV, "") or "bare"
    ttl_raw = env.get(_TTL_ENV, "")
    ttl = int(ttl_raw) if ttl_raw.isdigit() else 60
    if not key:
        from gateway.community.logger import get_logger

        get_logger("principal_signer").warning(
            "AVERNET_PRINCIPAL_SIGNING_KEY unset — using dev fallback key "
            "(NOT for production)."
        )
        key = _DEV_FALLBACK_KEY
    return PrincipalSignerConfig(signing_key=key, kid=kid, ttl_seconds=ttl)
