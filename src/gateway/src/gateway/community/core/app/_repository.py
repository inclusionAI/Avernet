"""``AppRepository`` — canonical ORM third-party-app registry.

One ORM implementation behind the
:class:`~gateway.community.spi.app.AppRegistry` SPI port. Resolves a presented
app token via the ``avernet_application`` table through the
:data:`~gateway.community.spi.database.DataSourcePlugin`'s sync ``orm_session``,
mapping the row to the SPI :class:`~gateway.community.spi.app.RegisteredApp` via
:meth:`AppRow.to_record`. Flavor-neutral — the ``DataSourcePlugin`` (bare
in-memory SQLite or a sofa real DB) is injected by the composition root, so this
single body runs unchanged across runtimes (mirrors ``BotRepository`` /
``AccessKeyRepository``).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from gateway.community.spi.app import AppRegistry, RegisteredApp
from gateway.community.spi.database import DataSourcePlugin

from ._orm import AppRow


class AppRepository(AppRegistry):
    """App table access (read + write) for ``avernet_application``.

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
        app_name: str,
        owners: str,
        app_type: str,
        tenant: str,
        status: str = "ACTIVE",
        env: str = "",
        config: dict[str, Any] | None = None,
        creator: str | None = None,
        modifier: str | None = None,
    ) -> int:
        """Persist a freshly registered app; return its inserted surrogate ``id``.

        ``token`` is the app's JWT (the unique lookup key). Optional ``status`` /
        ``env`` / ``config`` default to ``ACTIVE`` / ``""`` / ``{}``; ``creator`` /
        ``modifier`` default to ``None`` (the unauthenticated admin has no caller).
        """
        with self._db.orm_session() as session:
            row = AppRow(
                token=token,
                app_name=app_name,
                app_type=app_type,
                owners=owners,
                tenant=tenant,
                status=status,
                env=env,
                config={} if config is None else config,
                creator=creator,
                modifier=modifier,
            )
            session.add(row)
            session.flush()
            return row.id
