"""ORM model for the bot registry (``bcs_bots`` table) — canonical schema.

The canonical :class:`BotRepository` resolves a presented token to a bot row
keyed by ``token``. Registered on the shared
:class:`~gateway.community.spi.database.Base` so ``DataSourcePlugin.create_all()``
creates the table. :meth:`BotRow.to_record` maps a row onto the SPI
:class:`~gateway.community.spi.bot.RegisteredBot`.
"""

from __future__ import annotations

from sqlalchemy.orm import Mapped, mapped_column

from gateway.community.spi.bot import RegisteredBot
from gateway.community.spi.database import Base


class BotRow(Base):  # type: ignore[misc]
    """A bot resolvable by token (the ``bcs_bots`` table)."""

    __tablename__ = "bcs_bots"

    token: Mapped[str] = mapped_column(primary_key=True)
    bot_uuid: Mapped[str] = mapped_column()
    owner_id: Mapped[str] = mapped_column()
    tenant: Mapped[str] = mapped_column()

    def to_record(self) -> RegisteredBot:
        """Map this row onto the SPI :class:`RegisteredBot`."""
        return RegisteredBot(
            bot_uuid=self.bot_uuid, owner_id=self.owner_id, tenant=self.tenant
        )
