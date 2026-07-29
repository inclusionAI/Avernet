"""ORM model for the third-party-app registry (``avernet_apps`` table).

The canonical :class:`AppRepository` resolves a presented app token to an app
row keyed by ``token``. Registered on the shared
:class:`~gateway.community.spi.database.Base` so ``DataSourcePlugin.create_all()``
creates the table. :meth:`AppRow.to_record` maps a row onto the SPI
:class:`~gateway.community.spi.app.RegisteredApp`.
"""

from __future__ import annotations

from sqlalchemy.orm import Mapped, mapped_column

from gateway.community.spi.app import RegisteredApp
from gateway.community.spi.database import Base


class AppRow(Base):  # type: ignore[misc]
    """A third-party app resolvable by token (the ``avernet_apps`` table)."""

    __tablename__ = "avernet_apps"

    token: Mapped[str] = mapped_column(primary_key=True)
    app_id: Mapped[str] = mapped_column()
    app_name: Mapped[str] = mapped_column()
    owners: Mapped[str] = mapped_column()
    app_type: Mapped[str] = mapped_column()
    tenant: Mapped[str] = mapped_column()

    def to_record(self) -> RegisteredApp:
        """Map this row onto the SPI :class:`RegisteredApp`."""
        return RegisteredApp(
            app_id=self.app_id,
            app_name=self.app_name,
            owners=self.owners,
            app_type=self.app_type,
            tenant=self.tenant,
        )
