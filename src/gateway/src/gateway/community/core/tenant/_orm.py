"""ORM model for the tenant master table (``avernet_tenant``).

A standalone tenant registry. Registered on the shared
:class:`~gateway.community.spi.database.Base` so ``DataSourcePlugin.create_all()``
creates the table. ORM-only for now (no SPI/repository/HTTP) — add a registry
when a consumer appears.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, text
from sqlalchemy.orm import Mapped, mapped_column

from gateway.community.spi.database import Base


class TenantRow(Base):  # type: ignore[misc]
    """A tenant master row (the ``avernet_tenant`` table)."""

    __tablename__ = "avernet_tenant"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column()
    description: Mapped[str] = mapped_column(default="")
    owner: Mapped[str] = mapped_column(default="")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    creator: Mapped[str | None] = mapped_column(default=None)
    modifier: Mapped[str | None] = mapped_column(default=None)
    gmt_create: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP")
    )
    gmt_modified: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP")
    )
