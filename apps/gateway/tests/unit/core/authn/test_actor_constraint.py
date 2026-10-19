import pytest

from gateway.community.core.authn import IdentityChain, authenticate
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import (
    Bot,
    BotPrincipal,
    CredentialBundle,
    Presence,
    PrincipalType,
)


class FixedBot:
    name = "fixed"
    principal_type = PrincipalType.BOT

    def __init__(self, bot_id):
        self.bot_id = bot_id

    async def build(self, creds):
        if self.bot_id is None:
            return None
        return BotPrincipal(
            tenant="default",
            bot=Bot(
                bot_uuid=self.bot_id,
                owner_id="owner",
                token="test-token",
                app_id=0,
                agent_code="parent",
                tenant="default",
            ),
        )


def creds(actor):
    return CredentialBundle(headers={"x-bcn-actor-bot-id": actor}, cookies={}, query={})


@pytest.mark.parametrize("bot_id", [None, "bot_B"])
async def test_optional_bot_must_satisfy_actor(bot_id):
    registry = {
        PrincipalType.BOT: IdentityChain(PrincipalType.BOT, (FixedBot(bot_id),))
    }
    with pytest.raises(AuthError):
        await authenticate(
            creds("bot_A"), {PrincipalType.BOT: Presence.OPTIONAL}, registry
        )


async def test_actor_matching_optional_bot_succeeds():
    registry = {
        PrincipalType.BOT: IdentityChain(PrincipalType.BOT, (FixedBot("bot_A"),))
    }
    result = await authenticate(
        creds("bot_A"), {PrincipalType.BOT: Presence.OPTIONAL}, registry
    )
    assert result[PrincipalType.BOT].bot.bot_uuid == "bot_A"


@pytest.mark.parametrize("actor", ["", " ", "bot A"])
async def test_invalid_actor_is_rejected(actor):
    with pytest.raises(AuthError):
        await authenticate(creds(actor), {}, {})


async def test_actor_cannot_enable_bot_on_user_only_route():
    with pytest.raises(AuthError):
        await authenticate(creds("bot_A"), {PrincipalType.USER: Presence.REQUIRED}, {})


async def test_passthrough_retains_actor_for_backend_authentication():
    assert await authenticate(creds("bot_A"), {}, {}) == {}
