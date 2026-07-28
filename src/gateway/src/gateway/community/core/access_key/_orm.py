"""ORM model for the access-key registry (``access_keys`` table) — canonical schema.

The canonical :class:`AccessKeyRepository` resolves a presented token to an
access-key row keyed by ``token``. Registered on the shared
:class:`~gateway.community.spi.database.Base` so ``DataSourcePlugin.create_all()``
creates the table. :meth:`AccessKeyRow.to_record` maps a row onto the SPI
:class:`~gateway.community.spi.access_key.RegisteredAccessKey`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column

from gateway.community.spi.access_key import RegisteredAccessKey
from gateway.community.spi.database import Base


class AccessKeyRow(Base):  # type: ignore[misc]
    """An access key resolvable by token (the ``access_keys`` table)."""

    __tablename__ = "access_keys"

    token: Mapped[str] = mapped_column(primary_key=True)
    access_key_id: Mapped[str] = mapped_column()
    tenant: Mapped[str] = mapped_column()
    expire_at: Mapped[datetime] = mapped_column()

    def to_record(self) -> RegisteredAccessKey:
        """Map this row onto the SPI :class:`RegisteredAccessKey`."""
        return RegisteredAccessKey(
            access_key_id=self.access_key_id,
            tenant=self.tenant,
            expire_at=self.expire_at,
        )
