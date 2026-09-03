"""``oauth_session`` strategy — verify the ``bcs_session`` JWT cookie in place.

The gateway's browser/session path uses the same signed JWT that BCS issues at
login time. This strategy reads the ``bcs_session`` cookie, verifies its HS256
signature locally, and maps the ``sub`` claim onto a ``UserPrincipal``.

Absent cookie → ``None`` (not applicable).
Present but invalid cookie → ``AuthError`` (hard failure, no fallback).
"""

from __future__ import annotations

import jwt

from gateway.community.logger import get_logger
from gateway.community.spi.auth import AuthenticatedUser, AuthError
from gateway.community.spi.authn import (
    CredentialBundle,
    Principal,
    PrincipalType,
    UserPrincipal,
)
from gateway.community.spi.secret_resolver import SecretResolver

logger = get_logger("authn-oauth-session")

_BCS_SESSION_COOKIE = "bcs_session"
_BCS_SESSION_ALGORITHM = "HS256"


class OauthSessionStrategy:
    """Resolve a verified BCS session JWT into a ``UserPrincipal``."""

    name = "oauth_session"
    principal_type = PrincipalType.USER

    def __init__(
        self,
        *,
        jwt_secret: str | None = None,
        secret_resolver: SecretResolver | None = None,
        secret_name: str = "bcs_session_jwt_secret",
    ) -> None:
        self._jwt_secret = jwt_secret
        self._secret_resolver = secret_resolver
        self._secret_name = secret_name

    def _resolve_secret(self) -> str:
        if self._jwt_secret is not None:
            return self._jwt_secret
        if self._secret_resolver is None:
            raise AuthError("bcs session secret is unavailable")
        secret = self._secret_resolver.get_secret(self._secret_name)
        if secret is None:
            raise AuthError("bcs session secret is unavailable")
        value = getattr(secret, "secret_value", secret)
        if not isinstance(value, str) or not value.strip():
            raise AuthError("bcs session secret is unavailable")
        return value

    async def build(self, creds: CredentialBundle) -> Principal | None:
        token = creds.cookies.get(_BCS_SESSION_COOKIE, "").strip()
        if not token:
            return None

        try:
            claims = jwt.decode(
                token,
                self._resolve_secret(),
                algorithms=[_BCS_SESSION_ALGORITHM],
                options={"require": ["sub", "src", "iat", "exp"]},
            )
        except jwt.ExpiredSignatureError as exc:
            logger.warning("bcs_session cookie expired")
            raise AuthError(
                "bcs session cookie has expired, please log in again"
            ) from exc
        except jwt.PyJWTError as exc:
            logger.warning("bcs_session cookie verification failed")
            raise AuthError("invalid bcs session cookie") from exc

        try:
            subject_id = str(claims["sub"])
        except KeyError as exc:
            logger.warning("bcs_session cookie missing sub claim")
            raise AuthError("invalid bcs session cookie") from exc

        logger.debug(
            "bcs_session resolved: sub=%s src=%s", subject_id, claims.get("src")
        )
        return UserPrincipal(
            subject=AuthenticatedUser(
                id=subject_id,
                username=subject_id,
            )
        )
