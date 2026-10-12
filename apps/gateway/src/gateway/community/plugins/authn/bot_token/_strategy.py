"""``bot_token`` strategy — resolve a bot session token (mirrors BCS SessionTokenPlugin).

Token extraction:

- an ``Authorization: Bearer <token>`` (or a bare token) is the default source,
  but **only when it is NOT JWT-shaped** — a JWT is left for a (future) JWT-based
  identity strategy, never mistaken for a bot session token;
- a dedicated Bot header selects the Bot credential independently of User OAuth;
- absent/empty → not applicable (``None``).

Ordinary requests resolve the token in a **single** registry lookup:
``find_bot_by_token(token) → RegisteredBot | None``. There is no separate
validator abstraction — the lookup lives here. The registry record's
``bot_uuid`` / ``owner_id`` / ``app_id`` / ``agent_code`` / ``tenant`` flow
straight onto the SPI :class:`Bot` (plus the presented ``token``); ``owner_id``
is mapped from the DB ``created_by`` column.
"""

from __future__ import annotations

from gateway.community.logger import get_logger
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import (
    Bot,
    BotPrincipal,
    CredentialBundle,
    Principal,
    PrincipalType,
)
from gateway.community.spi.authn._actor import expected_actor_id
from gateway.community.spi.bot import BotRegistry

logger = get_logger("authn-bot-token")

_AUTH_HEADER = "authorization"


def is_jwt_format(token: str) -> bool:
    """Heuristic JWT shape: exactly three dot-separated segments."""
    return len(token.split(".")) == 3


def extract_bot_token(creds: CredentialBundle, dedicated_header: str) -> str | None:
    """Extract a dedicated Bot token, otherwise non-JWT ``Authorization``.

    A dedicated header is independent of user OAuth, which may be opaque.
    Duplicate Bot headers must agree. The strategy resolves a different
    Authorization credential before treating it as a second Bot token.
    """
    names = (
        ("x-avernet-bot-token", "x-bcs-bot-token")
        if dedicated_header in ("x-avernet-bot-token", "x-bcs-bot-token")
        else (dedicated_header,)
    )
    values: list[str] = [creds.headers.get(name, "").strip() for name in names if name]
    values = [value for value in values if value]
    if len(set(values)) > 1:
        raise AuthError("Conflicting Bot credentials")
    dedicated: str | None = values[0] if values else None
    auth: str = creds.headers.get(_AUTH_HEADER, "")
    if auth.lower().startswith("bearer"):
        token: str = auth[len("bearer") :].strip()
    else:
        token = auth.strip()
    if dedicated:
        return dedicated
    if token and not is_jwt_format(token):
        return token
    # No usable Authorization token → fall back to the dedicated header (as-is).
    return dedicated


class BotTokenStrategy:
    """Resolve a bot session token into a :class:`BotPrincipal`."""

    name = "bot_token"
    principal_type = PrincipalType.BOT

    def __init__(
        self, registry: BotRegistry, token_header: str = "x-avernet-bot-token"
    ) -> None:
        self._registry = registry
        self._token_header = token_header

    async def build(self, creds: CredentialBundle) -> Principal | None:
        token = extract_bot_token(creds, self._token_header)
        if token is None:
            return None  # no bot token → not applicable
        auth = creds.headers.get(_AUTH_HEADER, "").strip()
        if auth.lower().startswith("bearer "):
            auth = auth[len("bearer ") :].strip()
        # Opaque User OAuth is independent of a dedicated Bot credential.
        # Reject only when the second credential is also a verified Bot token.
        if auth and auth != token and not is_jwt_format(auth):
            if await self._registry.find_bot_by_token(auth) is not None:
                raise AuthError("Conflicting Bot credentials")
        actor = expected_actor_id(creds.headers)
        bot = (
            await self._registry.find_bot_by_token(token, actor)
            if actor is not None
            else await self._registry.find_bot_by_token(token)
        )
        if actor is not None and (bot is None or bot.bot_uuid != actor):
            raise AuthError("Actor does not match Bot token")
        if bot is None:
            logger.debug("bot token not found in registry")
            return None  # unknown / empty token → soft miss
        logger.debug(
            "bot token resolved: bot_uuid=%s tenant=%s", bot.bot_uuid, bot.tenant
        )
        return BotPrincipal(
            tenant=bot.tenant,
            bot=Bot(
                bot_uuid=bot.bot_uuid,
                owner_id=bot.owner_id,
                token=token,
                app_id=bot.app_id,
                agent_code=bot.agent_code,
                tenant=bot.tenant,
            ),
        )
