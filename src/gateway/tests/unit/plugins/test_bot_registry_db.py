"""DB-backed tests for ``BotRepository`` (queries the seeded ``bcs_bots`` table)."""

from __future__ import annotations

import pytest

from gateway.community.bootstrap._authn import build_database
from gateway.community.core.bot import BotRepository
from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin
from gateway.community.spi.bot import RegisteredBot


def _make_db():
    db = SqliteDatabasePlugin()
    return build_database(db)


@pytest.fixture(scope="module")
def registry() -> BotRepository:
    return BotRepository(_make_db())


async def test_known_token_resolves_seeded_bot(registry: BotRepository) -> None:
    bot = await registry.find_bot_by_token("bot-key")
    assert bot == RegisteredBot(bot_uuid="bot-7", owner_id="owner-1", tenant="t")


async def test_unknown_token_returns_none(registry: BotRepository) -> None:
    assert await registry.find_bot_by_token("nope") is None
