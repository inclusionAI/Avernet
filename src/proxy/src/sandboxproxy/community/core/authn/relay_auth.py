"""Relay WebSocket auth — token extraction, light/full verification, target binding.

Mirrors the enterprise ``relay_jwt`` seam without the Mist secret backend: the
HS256 shared secret comes from ``user_config.jwt.secret`` (config/env-driven in
the community build).

Two channels are accepted (in priority order):
1. ``X-PROXYPASS-TOKEN`` header, then ``x-proxypass-token`` query parameter.
2. ``Authorization: Bearer`` (mng-side fallback, carries the ``sno`` user id).

Two auth strengths:
- **full** (``/wsrelay/`` client side): signature + expiry + target + session.
- **light** (``/wsrevrelay/`` mng side): format + target + session only.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
import urllib.parse
from typing import Any, cast

from sandboxproxy.community.logger import get_logger

logger = get_logger("relay-auth")

_RE_WSRELAY_SESSION_ID = re.compile(r"/wsrelay/([^/?]+)")
_RE_WSREVRELAY_SESSION_ID = re.compile(r"/wsrevrelay/([^/?]+)")

PROXYPASS_TOKEN_HEADER = "X-PROXYPASS-TOKEN"
PROXYPASS_TOKEN_QUERY = "x-proxypass-token"


def b64url_decode(data: str) -> bytes:
    rem = len(data) % 4
    if rem:
        data += "=" * (4 - rem)
    return base64.urlsafe_b64decode(data)


def extract_token(headers: Any, path: str) -> str | None:
    """Extract the proxypass token from headers or the query string."""
    token = headers.get(PROXYPASS_TOKEN_HEADER)
    if token:
        return cast(str, token)

    parsed = urllib.parse.urlparse(path)
    tokens = urllib.parse.parse_qs(parsed.query).get(PROXYPASS_TOKEN_QUERY, [])
    if tokens:
        return tokens[0]
    return None


def extract_bearer_token(headers: Any) -> str | None:
    """Extract a bearer token from the ``Authorization`` header."""
    auth = headers.get("Authorization")
    if auth is None:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return cast(str, parts[1])


def extract_user_id(token: str) -> str | None:
    """Extract the ``sno`` user id from a JWT payload (no signature check)."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(b64url_decode(parts[1]))
    except (ValueError, json.JSONDecodeError):
        return None
    sno = payload.get("sno")
    if sno is None or sno == "":
        return None
    return str(sno)


def verify_token(token: str, secret: str) -> dict[str, Any] | None:
    """Verify an HS256 JWT signature + expiry; return the payload or ``None``."""
    if not secret:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, signature_b64 = parts

    try:
        header = json.loads(b64url_decode(header_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if header.get("alg") != "HS256":
        return None

    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    try:
        provided = b64url_decode(signature_b64)
    except (ValueError, binascii.Error):
        return None
    if not hmac.compare_digest(expected, provided):
        return None

    try:
        payload = json.loads(b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if exp is not None and time.time() > float(exp):
        return None
    return payload


def parse_target(target: str) -> tuple[str, str, str, str] | None:
    """Parse ``LOCAL_{device}@{template}:{port}:{session_id}`` into a 4-tuple."""
    if not target.startswith("LOCAL_"):
        return None
    rest = target[len("LOCAL_") :]
    at_idx = rest.find("@")
    if at_idx == -1:
        return None
    device = rest[:at_idx]
    tail = rest[at_idx + 1 :]
    parts = tail.split(":")
    if len(parts) < 3:
        return None
    session_id = parts[-1]
    port = parts[-2]
    template = ":".join(parts[:-2])
    return device, template, port, session_id


def session_id_from_path(path: str, endpoint: str) -> str | None:
    pattern = (
        _RE_WSRELAY_SESSION_ID if endpoint == "wsrelay" else _RE_WSREVRELAY_SESSION_ID
    )
    match = pattern.search(path)
    return match.group(1) if match else None


class RelayAuthResult:
    """Outcome of a relay auth check."""

    def __init__(
        self,
        *,
        ok: bool,
        status_code: int = 200,
        reason: str = "",
        session_id: str = "",
        user_id: str | None = None,
        token_source: str = "",
    ) -> None:
        self.ok = ok
        self.status_code = status_code
        self.reason = reason
        self.session_id = session_id
        self.user_id = user_id
        self.token_source = token_source

    @property
    def error(self) -> str:
        return self.reason


def authenticate_relay(
    headers: Any,
    path: str,
    endpoint: str,
    secret: str,
) -> RelayAuthResult:
    """Authenticate a relay WS connection for ``/wsrelay/`` or ``/wsrevrelay/``.

    ``endpoint`` selects full (client) vs light (mng) auth.
    """
    full = endpoint == "wsrelay"
    path_session_id = session_id_from_path(path, endpoint)

    token = extract_token(headers, path)
    token_source = PROXYPASS_TOKEN_HEADER.lower() if token else ""
    user_id: str | None = None

    if token is None:
        token = extract_bearer_token(headers)
        token_source = "authorization"
        if token is None:
            return RelayAuthResult(ok=False, status_code=401, reason="missing token")
        user_id = extract_user_id(token)
        if user_id is None:
            return RelayAuthResult(
                ok=False, status_code=401, reason="missing or invalid sno"
            )

    parts = token.split(".")
    if len(parts) != 3:
        return RelayAuthResult(ok=False, status_code=403, reason="invalid token format")

    if full:
        payload = verify_token(token, secret)
        if payload is None:
            return RelayAuthResult(ok=False, status_code=403, reason="invalid token")
    else:
        try:
            payload = json.loads(b64url_decode(parts[1]))
        except (ValueError, json.JSONDecodeError):
            return RelayAuthResult(
                ok=False, status_code=403, reason="invalid token format"
            )
        if not isinstance(payload, dict):
            return RelayAuthResult(
                ok=False, status_code=403, reason="invalid token format"
            )

    target = payload.get("target")
    if not target:
        return RelayAuthResult(
            ok=False, status_code=403, reason="missing target in token"
        )
    parsed = parse_target(target)
    if parsed is None:
        return RelayAuthResult(
            ok=False, status_code=403, reason="invalid target format"
        )

    if path_session_id is None or path_session_id != parsed[3]:
        return RelayAuthResult(ok=False, status_code=403, reason="session mismatch")

    return RelayAuthResult(
        ok=True,
        session_id=parsed[3],
        user_id=user_id,
        token_source=token_source,
    )
