"""``bot_token`` strategy — resolve a bot session token (mirrors BCS SessionTokenPlugin).

Token extraction:

- an ``Authorization: Bearer <token>`` (or a bare token) is the default source,
  but **only when it is NOT JWT-shaped** — a JWT is left for a (future) JWT-based
  identity strategy, never mistaken for a bot session token;
- otherwise the dedicated bot-token header (``x-avernet-bot-token``) is used,
  taken as-is;
- absent/empty → not applicable (``None``).

The strategy resolves the token itself in a **single** registry lookup:
``find_bot_by_token(token) → RegisteredBot | None``. There is no separate
validator abstraction — the lookup lives here. The registry record's
``bot_uuid`` / ``owner_id`` / ``app_id`` / ``agent_code`` / ``tenant`` flow
straight onto the SPI :class:`Bot` (plus the presented ``token``); ``owner_id``
is mapped from the DB ``created_by`` column.
"""

from __future__ import annotations

from gateway.community.logger import get_logger
from gateway.community.spi.authn import (
    Bot,
    BotPrincipal,
    CredentialBundle,
    Principal,
    PrincipalType,
)
from gateway.community.spi.bot import BotRegistry

logger = get_logger("authn-bot-token")

_AUTH_HEADER = "authorization"


def is_jwt_format(token: str) -> bool:
    """Heuristic JWT shape: exactly three dot-separated segments."""
    return len(token.split(".")) == 3


def extract_bot_token(creds: CredentialBundle, dedicated_header: str) -> str | None:
    """Extract a bot session token: ``Authorization`` first (non-JWT), else the dedicated header.

    ``Authorization`` (a Bearer or bare token) is the default source, but only
    when it is NOT JWT-shaped — a JWT is left for a (future) JWT-based identity
    strategy, never mistaken for a bot session token. If no usable Authorization
    token is present, the dedicated bot-token header is used as a fallback
    (taken as-is). Returns ``None`` when no usable bot token is present.
    """
    auth: str = creds.headers.get(_AUTH_HEADER, "")
    if auth.lower().startswith("bearer"):
        token: str = auth[len("bearer") :].strip()
    else:
        token = auth.strip()
    if token and not is_jwt_format(token):
        return token  # Authorization (Bearer or bare, non-JWT) wins
    # No usable Authorization token → fall back to the dedicated header (as-is).
    if dedicated_header:
        dedicated: str = creds.headers.get(dedicated_header, "").strip()
        if dedicated:
            return dedicated
    return None


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
        bot = await self._registry.find_bot_by_token(token)
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
