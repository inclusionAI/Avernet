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

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.sql import Select

from gateway.community.spi.auth import AuthError
from gateway.community.spi.bot import BotRegistry, RegisteredBot
from gateway.community.spi.database import DataSourcePlugin

from ._orm import BotRow, ProviderBindingRow, ProviderRow


class BotRepository(BotRegistry):
    """Environment-scoped credential lookup with an optional expected caller."""

    Model: type[BotRow] = BotRow

    def __init__(self, db: DataSourcePlugin, env: str = "dev") -> None:
        self._db = db
        self._env = env

    def _query(self) -> Select[tuple[BotRow, ProviderRow, ProviderBindingRow]]:
        bot, binding, provider = self.Model, ProviderBindingRow, ProviderRow
        return (
            select(bot, provider, binding)
            .outerjoin(
                binding, (binding.bot_uuid == bot.bot_uuid) & (binding.env == bot.env)
            )
            .outerjoin(
                provider,
                (
                    provider.provider_id
                    == func.coalesce(bot.provider_id, binding.provider_id)
                )
                & (provider.env == bot.env),
            )
            .where(bot.env == self._env, bot.is_deleted == 0, bot.actor_kind == "bot")
        )

    def _read(
        self, query: Select[tuple[BotRow, ProviderRow, ProviderBindingRow]]
    ) -> list[Row[Any]]:
        try:
            with self._db.orm_session() as session:
                rows = list(session.execute(query).all())
                # Retain loaded values across commit/close (Sofa may expire rows).
                session.expunge_all()
                return rows
        except AuthError:
            raise
        except Exception:
            raise AuthError("Bot identity store unavailable") from None

    @staticmethod
    def _record(row: Row[Any], *, agentpass: bool = False) -> RegisteredBot:
        bot: BotRow = row[0]
        provider: ProviderRow | None = row[1]
        binding: ProviderBindingRow | None = row[2]
        if (
            binding is not None
            and bot.provider_id is not None
            and (
                binding.provider_id != bot.provider_id
                or binding.provider_bot_ref != bot.provider_bot_ref
            )
        ):
            raise AuthError("Bot Provider binding mismatch")
        affiliated = bot.provider_id is not None or binding is not None
        if affiliated and provider is None:
            raise AuthError("Bot Provider is unavailable")
        if provider is not None:
            if provider.disabled or binding is not None and binding.disabled:
                raise AuthError("Bot Provider binding is disabled")
            downlink = provider.config.get("downlink")
            mode = downlink.get("auth_mode") if isinstance(downlink, dict) else None
            if agentpass and mode not in ("agentpass", "agentpass-subbots"):
                raise AuthError("Provider does not permit AgentPass authentication")
        return bot.to_record()

    async def find_bot_by_token(
        self, token: str, expected_actor_id: str | None = None
    ) -> RegisteredBot | None:
        query = self._query().where(self.Model.session_token == token)
        if expected_actor_id is not None:
            query = query.where(self.Model.bot_uuid == expected_actor_id)
        row = next(iter(self._read(query.limit(1))), None)
        return (
            None
            if row is None
            or row[0].session_token != token
            or expected_actor_id is not None
            and row[0].bot_uuid != expected_actor_id
            else self._record(row)
        )

    async def find_bot_by_agent_code(
        self, agent_code: str, expected_actor_id: str | None = None
    ) -> RegisteredBot | None:
        if not agent_code:
            return None
        query = self._query().where(self.Model.agent_code == agent_code)
        if expected_actor_id is not None:
            row = next(
                iter(
                    self._read(
                        query.where(self.Model.bot_uuid == expected_actor_id).limit(1)
                    )
                ),
                None,
            )
            if row is None:
                return None
            if row[0].agent_code != agent_code or row[0].bot_uuid != expected_actor_id:
                raise AuthError("AgentPass Bot identity mismatch")
            return self._record(row, agentpass=True)
        implicit = query.where(
            or_(
                (self.Model.provider_id.is_(None)) & (ProviderBindingRow.id.is_(None)),
                ProviderRow.config["downlink"]["auth_mode"].as_string() == "agentpass",
            )
        )
        rows = self._read(implicit.limit(2))
        if len(rows) > 1:
            raise AuthError("AgentPass default Bot is ambiguous")
        if rows:
            if rows[0][0].agent_code != agent_code:
                raise AuthError("AgentPass parent identity mismatch")
            return self._record(rows[0], agentpass=True)
        row = next(iter(self._read(query.limit(1))), None)
        if row is not None:
            raise AuthError("ActorRequired: parent AgentPass requires a Bot Actor")
        return None
