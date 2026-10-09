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

from sqlalchemy import JSON, BigInteger, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from gateway.community.spi.app import RegisteredApp
from gateway.community.spi.database import Base

# How many leading characters of an API key form its lookup prefix. A property
# of the credential scheme, not of storage — but it cannot live in ``_key_gen``,
# whose code must stay identical to secbaas's copy, so it is defined here (the
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
    app_name: Mapped[str] = mapped_column(String(256))
    app_type: Mapped[str] = mapped_column(String(64))
    api_key_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    api_key_prefix: Mapped[str | None] = mapped_column(
        String(API_KEY_PREFIX_LEN), unique=True, nullable=True
    )
    # DEPRECATED — legacy plaintext JWT credential; see the module docstring.
    token: Mapped[str | None] = mapped_column(String(700), unique=True, nullable=True)
    owners: Mapped[str] = mapped_column(String(1024))
    tenant: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    env: Mapped[str] = mapped_column(String(64), default="")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    creator: Mapped[str] = mapped_column(String(128), default="")
    modifier: Mapped[str] = mapped_column(String(128), default="")
    gmt_create: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP")
    )
    gmt_modified: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        # An app is named once per environment. ``app_name`` is how a human
        # picks their application out of a listing, and two rows sharing one in
        # the same ``env`` make every such listing ambiguous — including the one
        # a migrating caller reads to confirm their key landed.
        #
        # ``env`` is in the key and not merely alongside it: one database backs
        # several environments, and the same application legitimately exists in
        # each. Keyed on ``app_name`` alone, registering "billing" in dev would
        # lock the name out of prod.
        #
        # 1280 bytes at utf8mb4 (256x4 + 64x4), comfortably inside InnoDB's
        # 3072-byte cap, so neither column is squeezed by it.
        #
        # This key REPLACES the plain ``idx_avernet_application_app_name`` that
        # stood here (``index=True`` on the column above). ``app_name`` leads the
        # key, so a B-tree prefix scan serves every lookup that index served;
        # keeping both would maintain two structures for one access path.
        UniqueConstraint("app_name", "env", name="uk_avernet_application_app_name_env"),
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
