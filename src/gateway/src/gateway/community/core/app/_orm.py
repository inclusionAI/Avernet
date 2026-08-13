"""ORM model for the third-party-app registry (``avernet_application`` table).

The canonical :class:`AppRepository` resolves a presented credential to an app
row (surrogate ``id`` PK). A row carries **exactly one** credential form:

* ``api_key_hash`` + ``api_key_prefix`` — the current scheme. The prefix is the
  key's first 8 characters and is the lookup key (the hash is salted, so it
  cannot be one); the hash is one-way, so the table holds nothing usable.
* ``token`` — a plaintext JWT, **deprecated**. Populated only on rows predating
  the API-key scheme, served by an exact-match path kept for a transition
  window. Drop this column, its unique index, and
  ``AppRepository._by_legacy_token`` once the deprecation warning that path
  emits has gone quiet.

All three are nullable because "which credential form is this row" is a real
state, not an unknown. Nullability also matters for ``api_key_prefix``
specifically: a unique index permits many ``NULL``s but only one ``''``, so an
empty-string sentinel would collide across every legacy row.

Registered on the shared :class:`~gateway.community.spi.database.Base` so
``DataSourcePlugin.create_all()`` creates the table. :meth:`AppRow.to_record`
maps a row onto the SPI :class:`~gateway.community.spi.app.RegisteredApp` (core
fields only; ``status`` / ``env`` / ``config`` / audit columns stay DB-side).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from gateway.community.spi.app import RegisteredApp
from gateway.community.spi.database import Base

# How many leading characters of an API key form its lookup prefix. A property
# of the credential scheme, not of storage — but it cannot live in ``_key_gen``,
# which must stay byte-identical to secbaas's copy, so it is defined here (the
# module with no intra-package imports) and used by the ORM column width, the
# repository's lookups, and the registrar's slicing.
#
# The hand-written MySQL DDL is a SECOND source of truth: `migrations/mysql/`
# hardcodes varchar(8), and only the community SQLite path builds its schema
# from this constant. Raising it here without editing those files truncates
# stored prefixes in MySQL while every SQLite test passes, because SQLite
# ignores VARCHAR widths.
API_KEY_PREFIX_LEN = 8


class AppRow(Base):  # type: ignore[misc]
    """A third-party app resolvable by credential (the ``avernet_application`` table)."""

    __tablename__ = "avernet_application"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    app_name: Mapped[str] = mapped_column(index=True)
    app_type: Mapped[str] = mapped_column()
    api_key_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    api_key_prefix: Mapped[str | None] = mapped_column(
        String(API_KEY_PREFIX_LEN), unique=True, nullable=True
    )
    # DEPRECATED — legacy plaintext JWT credential; see the module docstring.
    token: Mapped[str | None] = mapped_column(unique=True, nullable=True)
    owners: Mapped[str] = mapped_column()
    tenant: Mapped[str] = mapped_column()
    status: Mapped[str] = mapped_column(default="ACTIVE")
    env: Mapped[str] = mapped_column(default="")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    creator: Mapped[str] = mapped_column(default="")
    modifier: Mapped[str] = mapped_column(default="")
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
