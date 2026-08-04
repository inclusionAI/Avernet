"""DB-backed tests for ``BotRepository`` (queries the seeded ``bcs_bots`` table)."""

from __future__ import annotations

import pytest

from gateway.community.bootstrap import initialize_database
from gateway.community.bootstrap._configs import DatabaseConfig
from gateway.community.core.bot import BotRepository
from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin
from gateway.community.spi.bot import RegisteredBot


def _make_db():
    db = SqliteDatabasePlugin()
    return initialize_database(db, DatabaseConfig(plugin_type="SQLITE_ORM", db_url=""))


@pytest.fixture(scope="module")
def registry() -> BotRepository:
    return BotRepository(_make_db())


async def test_known_token_resolves_seeded_bot(registry: BotRepository) -> None:
    bot = await registry.find_bot_by_token("bot-key")
    assert bot == RegisteredBot(
        bot_uuid="bot-7",
        owner_id="owner-1",
        app_id=-1,
        agent_code="agent-1",
        tenant="default",
    )


async def test_unknown_token_returns_none(registry: BotRepository) -> None:
    assert await registry.find_bot_by_token("nope") is None


async def test_known_agent_code_resolves_seeded_bot(registry: BotRepository) -> None:
    bot = await registry.find_bot_by_agent_code("agent-1")
    assert bot == RegisteredBot(
        bot_uuid="bot-7",
        owner_id="owner-1",
        app_id=-1,
        agent_code="agent-1",
        tenant="default",
    )


async def test_unknown_agent_code_returns_none(registry: BotRepository) -> None:
    assert await registry.find_bot_by_agent_code("nope") is None


async def test_empty_agent_code_returns_none(registry: BotRepository) -> None:
    assert await registry.find_bot_by_agent_code("") is None
