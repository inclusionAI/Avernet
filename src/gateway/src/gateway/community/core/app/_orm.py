"""ORM model for the third-party-app registry (``avernet_application`` table).

The canonical :class:`AppRepository` resolves a presented app token to an app
row (surrogate ``id`` PK; ``token`` is the unique lookup key). Registered on the shared
:class:`~gateway.community.spi.database.Base` so ``DataSourcePlugin.create_all()``
creates the table. :meth:`AppRow.to_record` maps a row onto the SPI
:class:`~gateway.community.spi.app.RegisteredApp` (core fields only; ``status``
/ ``env`` / ``config`` / audit columns stay DB-side).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, text
from sqlalchemy.orm import Mapped, mapped_column

from gateway.community.spi.app import RegisteredApp
from gateway.community.spi.database import Base


class AppRow(Base):  # type: ignore[misc]
    """A third-party app resolvable by token (the ``avernet_application`` table)."""

    __tablename__ = "avernet_application"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    app_name: Mapped[str] = mapped_column(index=True)
    app_type: Mapped[str] = mapped_column()
    token: Mapped[str] = mapped_column(unique=True)
    owners: Mapped[str] = mapped_column()
    tenant: Mapped[str] = mapped_column()
    status: Mapped[str] = mapped_column(default="ACTIVE")
    env: Mapped[str] = mapped_column(default="")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    creator: Mapped[str | None] = mapped_column(default=None)
    modifier: Mapped[str | None] = mapped_column(default=None)
    gmt_create: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP")
    )
    gmt_modified: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP")
    )

    def to_record(self) -> RegisteredApp:
        """Map this row onto the SPI :class:`RegisteredApp` (core fields only)."""
        return RegisteredApp(
            id=self.id,
            app_name=self.app_name,
            owners=self.owners,
            app_type=self.app_type,
            tenant=self.tenant,
        )
