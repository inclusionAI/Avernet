"""ORM model for the access-key registry (``avernet_access_key_token`` table) — canonical schema.

The canonical :class:`AccessKeyRepository` resolves a presented token to an
access-key row (surrogate ``id`` PK; ``token`` is the unique lookup key). Registered on the shared
:class:`~gateway.community.spi.database.Base` so ``DataSourcePlugin.create_all()``
creates the table. :meth:`AccessKeyRow.to_record` maps a row onto the SPI
:class:`~gateway.community.spi.access_key.RegisteredAccessKey` (core fields only;
audit columns stay DB-side, like ``bcs_bots``'s ``env``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, text
from sqlalchemy.orm import Mapped, mapped_column

from gateway.community.spi.access_key import RegisteredAccessKey
from gateway.community.spi.database import Base


class AccessKeyRow(Base):  # type: ignore[misc]
    """An access key resolvable by token (the ``avernet_access_key_token`` table)."""

    __tablename__ = "avernet_access_key_token"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(unique=True)
    access_key: Mapped[str] = mapped_column()
    tenant: Mapped[str] = mapped_column()
    expire_at: Mapped[datetime] = mapped_column()
    creator: Mapped[str] = mapped_column(default="")
    modifier: Mapped[str] = mapped_column(default="")
    gmt_create: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP")
    )
    gmt_modified: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP")
    )

    def to_record(self) -> RegisteredAccessKey:
        """Map this row onto the SPI :class:`RegisteredAccessKey` (core fields only)."""
        return RegisteredAccessKey(
            access_key=self.access_key,
            tenant=self.tenant,
            expire_at=self.expire_at,
        )
