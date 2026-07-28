"""``AccessKeyRepository`` — canonical ORM access-key registry.

One ORM implementation behind the
:class:`~gateway.community.spi.access_key.AccessKeyRegistry` SPI port. Resolves a
presented access-key token via the ``access_keys`` table through the
:data:`~gateway.community.spi.database.DataSourcePlugin`'s sync ``orm_session``,
mapping the row to the SPI
:class:`~gateway.community.spi.access_key.RegisteredAccessKey` via
:meth:`AccessKeyRow.to_record`. Flavor-neutral — the ``DataSourcePlugin`` (bare
in-memory SQLite or a sofa real DB) is injected by the composition root, so this
single body runs unchanged across runtimes (mirrors backend
``BotFriendRepository``).
"""

from __future__ import annotations

from gateway.community.spi.access_key import AccessKeyRegistry, RegisteredAccessKey
from gateway.community.spi.database import DataSourcePlugin

from ._orm import AccessKeyRow


class AccessKeyRepository(AccessKeyRegistry):
    """Resolve an access-key token against the ``access_keys`` table (canonical)."""

    Model: type[AccessKeyRow] = AccessKeyRow

    def __init__(self, db: DataSourcePlugin) -> None:
        self._db = db

    async def find_access_key_by_token(self, token: str) -> RegisteredAccessKey | None:
        with self._db.orm_session() as session:
            row = session.get(self.Model, token)
            return None if row is None else row.to_record()
