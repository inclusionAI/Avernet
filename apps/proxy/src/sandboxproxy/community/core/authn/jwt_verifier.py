"""JWT verification for the proxy edge."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, cast

from sandboxproxy.community.logger import get_logger

logger = get_logger("authn")


class JwtVerifier:
    """Verify HS256 JWTs signed with a configured shared secret."""

    def __init__(self, secret: str) -> None:
        self._secret = secret.encode("utf-8")

    @classmethod
    def from_secret(cls, secret: str) -> JwtVerifier:
        if not secret:
            raise ValueError("JWT verification secret is empty")
        return cls(secret)

    def _b64url_decode(self, value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)

    def verify(self, token: str) -> dict[str, Any] | None:
        parts = token.split(".")
        if len(parts) != 3:
            logger.debug("malformed token: %d parts", len(parts))
            return None
        header_b64, payload_b64, signature_b64 = parts

        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        expected = hmac.new(self._secret, signing_input, hashlib.sha256).digest()
        try:
            provided = self._b64url_decode(signature_b64)
        except Exception:
            logger.debug("invalid signature encoding")
            return None
        if not hmac.compare_digest(expected, provided):
            logger.debug("token signature mismatch")
            return None

        try:
            payload_bytes = self._b64url_decode(payload_b64)
            payload = json.loads(payload_bytes.decode("utf-8"))
        except Exception:
            logger.debug("invalid payload encoding")
            return None

        if not isinstance(payload, dict):
            return None
        exp = payload.get("exp")
        if exp is not None and time.time() > float(exp):
            logger.debug("token expired")
            return None
        return cast(dict[str, Any], payload)
