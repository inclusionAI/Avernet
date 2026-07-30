"""``AppRepository`` — canonical ORM third-party-app registry.

One ORM implementation behind the
:class:`~gateway.community.spi.app.AppRegistry` SPI port. Resolves a presented
app token via the ``avernet_apps`` table through the
:data:`~gateway.community.spi.database.DataSourcePlugin`'s sync ``orm_session``,
mapping the row to the SPI :class:`~gateway.community.spi.app.RegisteredApp` via
:meth:`AppRow.to_record`. Flavor-neutral — the ``DataSourcePlugin`` (bare
in-memory SQLite or a sofa real DB) is injected by the composition root, so this
single body runs unchanged across runtimes (mirrors ``BotRepository`` /
``AccessKeyRepository``).
"""

from __future__ import annotations

from sqlalchemy import select

from gateway.community.spi.app import AppRegistry, RegisteredApp
from gateway.community.spi.database import DataSourcePlugin

from ._orm import AppRow


class AppRepository(AppRegistry):
    """App table access (read + write) for ``avernet_apps``.

    Resolves a presented token (read) and persists a freshly registered app
    (write) — all DB touch lives here, never in the registrar.
    """

    Model: type[AppRow] = AppRow

    def __init__(self, db: DataSourcePlugin) -> None:
        self._db = db

    async def find_app_by_token(self, token: str) -> RegisteredApp | None:
        with self._db.orm_session() as session:
            row = session.scalar(select(self.Model).where(self.Model.token == token))
            return None if row is None else row.to_record()

    async def store(
        self,
        *,
        token: str,
        app_id: str,
        app_name: str,
        owners: str,
        app_type: str,
        tenant: str,
    ) -> None:
        """Persist a freshly registered app (``token`` = its JWT, the unique lookup key)."""
        with self._db.orm_session() as session:
            session.add(
                AppRow(
                    token=token,
                    app_id=app_id,
                    app_name=app_name,
                    owners=owners,
                    app_type=app_type,
                    tenant=tenant,
                )
            )
