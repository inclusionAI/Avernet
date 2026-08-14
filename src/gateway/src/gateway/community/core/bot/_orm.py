"""ORM model for the bot registry (``bcs_bots`` table) — canonical schema.

The canonical :class:`BotRepository` resolves a presented session token to a bot
row (surrogate ``id`` PK; ``session_token`` is the unique lookup key). Registered on the shared
:class:`~gateway.community.spi.database.Base` so ``DataSourcePlugin.create_all()``
creates the table. :meth:`BotRow.to_record` maps a row onto the SPI
:class:`~gateway.community.spi.bot.RegisteredBot` (core fields only; ``env`` stays
DB-side).
"""

from __future__ import annotations

from sqlalchemy.orm import Mapped, mapped_column

from gateway.community.spi.bot import RegisteredBot
from gateway.community.spi.database import Base


class BotRow(Base):  # type: ignore[misc]
    """A bot resolvable by session token (the ``bcs_bots`` table)."""

    __tablename__ = "bcs_bots"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_token: Mapped[str] = mapped_column(unique=True)
    bot_uuid: Mapped[str] = mapped_column()
    env: Mapped[str] = mapped_column()
    created_by: Mapped[str] = mapped_column()
    agent_code: Mapped[str] = mapped_column()

    # TODO
    # app_id: Mapped[int] = mapped_column(BigInteger)
    # tenant: Mapped[str] = mapped_column()

    def to_record(self) -> RegisteredBot:
        """Map this row onto the SPI :class:`RegisteredBot` (core fields only)."""
        return RegisteredBot(
            bot_uuid=self.bot_uuid,
            owner_id=self.created_by,
            app_id=-1,
            agent_code=self.agent_code,
            tenant="default",
        )
