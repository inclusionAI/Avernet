"""``bot_token`` strategy — resolve a bot session token (mirrors BCS SessionTokenPlugin).

Token extraction mirrors BCS ``bcs-auth-session::SessionTokenPlugin``:

- the dedicated bot-token header (``x-bot-token``) **wins** and is taken as-is
  when non-empty;
- otherwise an ``Authorization: Bearer <token>`` (or a bare token) is used, but
  **only when it is NOT JWT-shaped** — a JWT is left for a (future) JWT-based
  identity strategy, never mistaken for a bot session token;
- absent/empty → not applicable (``None``).

The strategy resolves the token itself in a **single** registry lookup:
``find_bot_by_token(token) → Bot | None`` (the registry indexes the token
directly against the bot record). There is no separate ``BotTokenValidator``
abstraction — the lookup lives here.

Behaviour:

- no bot token → ``None`` (not applicable; runner fails-closed for ``bot``);
- a token whose bot is unknown / empty → ``None`` (soft miss, like BCS
  ``find_bot_by_token → None``);
- a resolved bot → :class:`BotPrincipal` carrying the raw token downstream.
"""

from __future__ import annotations

from gateway.community.spi.authn import (
    BotPrincipal,
    CredentialBundle,
    Principal,
    PrincipalType,
)

from ._registry import BotRegistry

_AUTH_HEADER = "authorization"


def is_jwt_format(token: str) -> bool:
    """Heuristic JWT shape: exactly three dot-separated segments (mirrors BCS jwt.rs)."""
    return len(token.split(".")) == 3


def extract_bot_token(creds: CredentialBundle, dedicated_header: str) -> str | None:
    """Extract a bot session token from the request (mirrors BCS extract_bot_token).

    Returns ``None`` when no usable bot token is present.
    """
    if dedicated_header:
        dedicated = creds.headers.get(dedicated_header, "").strip()
        if dedicated:
            return dedicated  # dedicated header wins; taken as-is (no JWT check)
    auth = creds.headers.get(_AUTH_HEADER, "")
    # Mirror BCS `strip_prefix("Bearer ").unwrap_or(auth).trim()`: strip an
    # optional Bearer scheme (case-insensitive on the scheme keyword), else take
    # the bare value, then trim.
    if auth.lower().startswith("bearer"):
        token = auth[len("bearer") :].strip()
    else:
        token = auth.strip()
    if not token or is_jwt_format(token):
        return None  # empty or JWT-shaped → not a bot session token
    return token


class BotTokenStrategy:
    """Resolve a bot session token into a :class:`BotPrincipal` (mirrors BCS)."""

    name = "bot_token"
    principal_type = PrincipalType.BOT

    def __init__(self, registry: BotRegistry, token_header: str) -> None:
        self._registry = registry
        self._token_header = token_header

    async def build(self, creds: CredentialBundle) -> Principal | None:
        token = extract_bot_token(creds, self._token_header)
        if token is None:
            return None  # no bot token → not applicable
        bot = await self._registry.find_bot_by_token(token)
        if bot is None:
            return None  # unknown / empty token → soft miss
        return BotPrincipal(
            tenant=bot.tenant,
            bot_uuid=bot.bot_uuid,
            owner_id=bot.owner_id,
            token=token,
        )
