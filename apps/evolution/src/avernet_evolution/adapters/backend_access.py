"""Backend's short-lived Bot authorization receipt, NOT a user login token.

The existing Gateway principal signing key is provisioned by deployment; worker
keys and browser cookies are never used here. One tenant per Evolution database.
"""

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import asdict

import jwt
from avernet_genome.models import BotRef

from ..plugin_api import AccessGrant

HEADER = "X-Avernet-Evolution-Grant"


def fingerprint(method: str, path: str, query: str, body: bytes, headers: Mapping[str, str]) -> str:
    material = json.dumps(
        [
            method,
            path,
            query,
            hashlib.sha256(body).hexdigest(),
            headers.get("if-match", ""),
            headers.get("idempotency-key", ""),
        ],
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode()).hexdigest()


def issue_grant(*, key: str, bot: BotRef, actor: str, tenant: str, request_hash: str) -> str:
    if len(key.encode()) < 32 or not actor or not tenant:
        raise ValueError("Backend authorization receipt is not configured")
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "backend",
            "aud": "evolution",
            "iat": now,
            "exp": now + 60,
            "bot": asdict(bot),
            "actor": actor,
            "tenant": tenant,
            "request_hash": request_hash,
        },
        key,
        algorithm="HS256",
        headers={"typ": "avernet-evolution-grant", "kid": "bare"},
    )


class BackendAccess:
    """Consume an authorization decision already made by the shared Bot gate."""

    def __init__(self, *, key: str, tenant: str):
        if len(key.encode()) < 32 or not tenant.strip():
            raise ValueError("Backend authorization receipt is not configured")
        self.key, self.tenant = key, tenant

    def verify(self, token: str, *, bot_id: str, owner_hint: str, request_hash: str) -> AccessGrant:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("typ") != "avernet-evolution-grant" or header.get("kid") != "bare":
                raise ValueError("wrong receipt type")
            claims = jwt.decode(
                token,
                self.key,
                algorithms=["HS256"],
                issuer="backend",
                audience="evolution",
                options={"require": ["iat", "exp", "iss", "aud", "bot", "actor", "tenant", "request_hash"]},
            )
            bot = BotRef(**claims["bot"])
            if (
                claims["tenant"] != self.tenant
                or claims["request_hash"] != request_hash
                or bot.bot_id != bot_id
                or owner_hint != bot.entity_id
                or not isinstance(claims["actor"], str)
                or not claims["actor"].strip()
                or claims["exp"] - claims["iat"] > 60
            ):
                raise ValueError("receipt scope mismatch")
            return AccessGrant(bot=bot, actor=claims["actor"])
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise PermissionError("Backend authorization receipt required") from None
