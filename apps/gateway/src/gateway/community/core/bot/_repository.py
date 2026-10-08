"""``BotRepository`` — canonical ORM bot registry.

One ORM implementation behind the :class:`~gateway.community.spi.bot.BotRegistry`
SPI port. Resolves a presented bot token via the ``bcs_bots`` table through the
:data:`~gateway.community.spi.database.DataSourcePlugin`'s sync ``orm_session``,
mapping the row to the SPI :class:`~gateway.community.spi.bot.RegisteredBot` via
:meth:`BotRow.to_record`. Flavor-neutral — the ``DataSourcePlugin`` (bare
in-memory SQLite or a sofa real DB) is injected by the composition root, so this
single body runs unchanged across runtimes (mirrors backend ``BotFriendRepository``).
"""

from __future__ import annotations

from sqlalchemy import select

from gateway.community.spi.bot import BotRegistry, RegisteredBot
from gateway.community.spi.database import DataSourcePlugin

from ._orm import BotRow


class BotRepository(BotRegistry):
    """Resolve a bot token against the ``bcs_bots`` table (canonical ORM impl)."""

    Model: type[BotRow] = BotRow

    def __init__(self, db: DataSourcePlugin) -> None:
        self._db = db

    async def find_bot_by_token(self, token: str) -> RegisteredBot | None:
        with self._db.orm_session() as session:
            row = session.scalar(
                select(self.Model).where(self.Model.session_token == token)
            )
            return None if row is None else row.to_record()

    async def find_bot_by_agent_code(self, agent_code: str) -> RegisteredBot | None:
        """Resolve a bot by its ``agent_code`` column (soft miss on unknown)."""
        if not agent_code:
            return None
        with self._db.orm_session() as session:
            row = session.scalar(
                select(self.Model).where(self.Model.agent_code == agent_code)
            )
            return None if row is None else row.to_record()
