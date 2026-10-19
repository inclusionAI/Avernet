import pytest

from gateway.community.bootstrap import initialize_database
from gateway.community.core.bot import BotRepository, BotRow
from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin
from gateway.community.spi.auth import AuthError


def db_with_bots():
    db = initialize_database(SqliteDatabasePlugin())
    with db.orm_session() as session:
        for index, env in [(1, "dev"), (2, "dev"), (3, "prod")]:
            session.add(
                BotRow(
                    id=index,
                    session_token=f"token_{index}",
                    bot_uuid=f"bot_{index}",
                    env=env,
                    created_by="owner",
                    agent_code="parent",
                )
            )
    return db


async def test_parent_lookup_rejects_ambiguous_default_bots():
    repo = BotRepository(db_with_bots())
    with pytest.raises(AuthError):
        await repo.find_bot_by_agent_code("parent")


async def test_actor_selects_only_matching_parent_and_environment():
    repo = BotRepository(db_with_bots())
    bot = await repo.find_bot_by_agent_code("parent", "bot_2")
    assert bot.bot_uuid == "bot_2"
    assert await repo.find_bot_by_agent_code("other", "bot_2") is None
    assert await repo.find_bot_by_agent_code("parent", "bot_3") is None


async def test_static_token_is_constrained_by_actor_and_environment():
    repo = BotRepository(db_with_bots())
    assert (await repo.find_bot_by_token("token_1", "bot_1")).bot_uuid == "bot_1"
    assert await repo.find_bot_by_token("token_1", "bot_2") is None
    assert await repo.find_bot_by_token("token_3") is None


async def test_static_token_strategy_cannot_switch_actor():
    from gateway.community.plugins.authn.bot_token import BotTokenStrategy
    from gateway.community.spi.authn import CredentialBundle

    strategy = BotTokenStrategy(BotRepository(db_with_bots()))
    creds = CredentialBundle(
        headers={"authorization": "Bearer token_1", "x-bcn-actor-bot-id": "bot_2"},
        cookies={},
        query={},
    )
    with pytest.raises(AuthError):
        await strategy.build(creds)


async def test_single_subbot_requires_actor_but_static_token_does_not():
    from gateway.community.core.bot._orm import ProviderRow

    db = db_with_bots()
    with db.orm_session() as session:
        session.add(
            ProviderRow(
                id=1,
                provider_id="provider",
                env="dev",
                config={"downlink": {"auth_mode": "agentpass-subbots"}},
                disabled=0,
            )
        )
        bot = session.get(BotRow, 1)
        bot.provider_id = "provider"
        bot.provider_bot_ref = "parent:reviewer"
        bot.agent_code = "new_parent"
    repo = BotRepository(db)
    with pytest.raises(AuthError, match="ActorRequired"):
        await repo.find_bot_by_agent_code("new_parent")
    assert (
        await repo.find_bot_by_agent_code("new_parent", "bot_1")
    ).bot_uuid == "bot_1"
    assert (await repo.find_bot_by_token("token_1")).bot_uuid == "bot_1"
