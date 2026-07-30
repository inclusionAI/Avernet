"""``AccessKeyRepository`` — canonical ORM access-key registry.

One ORM implementation behind the
:class:`~gateway.community.spi.access_key.AccessKeyRegistry` SPI port. Resolves a
presented access-key token via the ``baas_access_key_token`` table through the
:data:`~gateway.community.spi.database.DataSourcePlugin`'s sync ``orm_session``,
mapping the row to the SPI
:class:`~gateway.community.spi.access_key.RegisteredAccessKey` via
:meth:`AccessKeyRow.to_record`. Flavor-neutral — the ``DataSourcePlugin`` (bare
in-memory SQLite or a sofa real DB) is injected by the composition root, so this
single body runs unchanged across runtimes (mirrors backend
``BotFriendRepository``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from gateway.community.spi.access_key import AccessKeyRegistry, RegisteredAccessKey
from gateway.community.spi.database import DataSourcePlugin

from ._orm import AccessKeyRow


class AccessKeyRepository(AccessKeyRegistry):
    """Access-key table access (read + write) for ``baas_access_key_token``.

    Resolves a presented token (read) and persists a freshly issued access key
    (write) — all DB touch lives here, never in the issuer.
    """

    Model: type[AccessKeyRow] = AccessKeyRow

    def __init__(self, db: DataSourcePlugin) -> None:
        self._db = db

    async def find_access_key_by_token(self, token: str) -> RegisteredAccessKey | None:
        with self._db.orm_session() as session:
            row = session.scalar(select(self.Model).where(self.Model.token == token))
            return None if row is None else row.to_record()

    async def store(
        self,
        *,
        token: str,
        access_key: str,
        tenant: str,
        expire_at: datetime,
    ) -> None:
        """Persist a freshly issued access key (``token`` = its JWT, the unique lookup key)."""
        with self._db.orm_session() as session:
            session.add(
                AccessKeyRow(
                    token=token,
                    access_key=access_key,
                    tenant=tenant,
                    expire_at=expire_at,
                )
            )
