"""ORM mirrors of the two tables this migration touches that the gateway does not own.

Both are **foreign** tables, mapped here so one SQLAlchemy session can span the
whole migration. Neither is provisioned by the MariaDB plugin: its ``create_all``
whitelists the three ``avernet_*`` tables precisely so a transitively registered
foreign model is never created against a real deployment (the same treatment
``bcs_bots`` already gets). The community SQLite path does create them from this
metadata, which is what lets the migration be tested end to end without a live
MySQL.

``baas_api_key`` is read-only here — every column is mapped so a test can build
a realistic row, but nothing in this package writes one.

``ac_bot_app_grant`` and ``ac_bot_app_grant_log`` ARE written, and that write
crosses a module boundary: the backend (``agentclaw``) owns those tables. It is
done here on purpose and with a bounded lifetime — this endpoint exists to move
a finite population of secbaas keys once, and routing a one-shot backfill
through a new service-to-service API would outlive the thing it serves. The
definitions below must therefore track
``src/backend/src/agentclaw/community/core/bot_app_grant/models.py`` column for
column; drift between them is the failure this comment exists to prevent.

Two things the backend's own model does that this mirror deliberately does not:

* No ``register_avernet_tenant_guard``. That guard reads a *request* tenant out
  of the backend's context, which does not exist in this process. The tenant is
  therefore passed explicitly into every row the migrator builds, and is the
  single value :class:`~gateway.community.core.baas_migration.BaasKeyMigrator`
  is constructed with.
* No ``COLLATE utf8mb4_bin`` on ``user_id``. The deployed DDL pins it and the
  backend's ORM does not, for the reason its comment gives: SQLite has no such
  collation and ``create_all`` would fail. Every comparison is against a bound
  parameter rather than another column, so both runtimes agree regardless.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from gateway.community.spi.database import Base

#: Leading characters of an API key that form its lookup prefix.
#:
#: Duplicated from ``core.app`` rather than imported, because it is a property
#: of *secbaas's* table here, not of the gateway's. The two happen to agree —
#: which is exactly why a migrated key keeps verifying — but they are free to
#: diverge, and importing would hide that.
BAAS_API_KEY_PREFIX_LEN = 8

#: Column widths on the destination tables that the migration must respect.
#:
#: These are refusal thresholds, not truncation points. ``env`` is the tight one
#: and the reason this constant exists at all: ``baas_api_key.env`` is
#: ``varchar(32)`` while ``ac_bot_app_grant.env`` is ``varchar(20)``, so a
#: perfectly valid source row can carry an ``env`` the destination cannot hold.
#: Under a permissive SQL mode that write would silently truncate and produce a
#: grant no request can ever resolve.
GRANT_ENV_MAX_LENGTH = 20
GRANT_IDENTITY_MAX_LENGTH = 256
GRANT_APP_NAME_MAX_LENGTH = 1024

#: Width of ``avernet_application.app_name`` — the gateway's own column, and the
#: tighter of the two the migration writes a name into (the grant tables allow
#: 1024). Named here so the migrator can refuse an over-long name with the same
#: message as every other overflow instead of letting the driver decide.
APP_NAME_MAX_LENGTH = 256


class BaasApiKeyRow(Base):  # type: ignore[misc]
    """A secbaas API key (the ``baas_api_key`` table) — read-only.

    ``api_key_prefix`` is unique upstream, so a prefix lookup returns at most one
    row; the hash is salted PBKDF2, which is why the prefix has to be the lookup
    key in the first place.
    """

    __tablename__ = "baas_api_key"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    api_key_hash: Mapped[str] = mapped_column(String(128))
    api_key_prefix: Mapped[str] = mapped_column(
        String(BAAS_API_KEY_PREFIX_LEN), unique=True
    )
    key_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    app_id: Mapped[str] = mapped_column(String(128))
    app_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    rate_limit_rpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_limit_rpd: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    owner: Mapped[str] = mapped_column(String(64))
    tenant: Mapped[str | None] = mapped_column(String(64), nullable=True)
    env: Mapped[str] = mapped_column(String(32))
    creator: Mapped[str] = mapped_column(String(64))
    modifier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy: Mapped[str | None] = mapped_column(Text, nullable=True)
    gmt_create: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP")
    )
    gmt_modified: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP")
    )


class BotAppGrantRow(Base):  # type: ignore[misc]
    """A live bot→app authorization (the backend's ``ac_bot_app_grant`` table).

    A row means *"app A may act as user U on bot B, which O owns"*. For a
    migrated key ``user_id`` and ``owner_id`` are the same person — the
    ``entity_id`` half of secbaas's ``{bot_id}:{entity_id}`` reference — because
    the only delegation secbaas could express was over the caller's own bot.
    """

    __tablename__ = "ac_bot_app_grant"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(BigInteger)
    app_name: Mapped[str] = mapped_column(String(GRANT_APP_NAME_MAX_LENGTH))
    bot_id: Mapped[str] = mapped_column(String(GRANT_IDENTITY_MAX_LENGTH))
    user_id: Mapped[str] = mapped_column(String(GRANT_IDENTITY_MAX_LENGTH))
    owner_id: Mapped[str] = mapped_column(String(GRANT_IDENTITY_MAX_LENGTH))
    env: Mapped[str] = mapped_column(String(GRANT_ENV_MAX_LENGTH))
    avernet_tenant: Mapped[str] = mapped_column(String(64), server_default="teamclaw")
    gmt_create: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP")
    )
    gmt_modified: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        UniqueConstraint(
            "avernet_tenant",
            "app_id",
            "bot_id",
            "user_id",
            "env",
            name="uk_bot_app_grant_scope",
        ),
        Index(
            "idx_bot_app_grant_app_user",
            "avernet_tenant",
            "app_id",
            "user_id",
            "env",
        ),
        Index(
            "idx_bot_app_grant_bot_owner",
            "avernet_tenant",
            "bot_id",
            "owner_id",
            "env",
        ),
    )


class BotAppGrantLogRow(Base):  # type: ignore[misc]
    """Append-only authorization history (``ac_bot_app_grant_log``).

    Written alongside every live row rather than skipped, because the live row is
    not the record of *how* an authorization came to exist. "Who let this
    application in, and when" is read after the live row is gone — a migrated
    grant with no log entry is one whose provenance can never be answered, which
    is precisely the gap this table exists to close.

    No unique key, by the backend's design: this table's job is to accept every
    event, including the fourth revocation of one pair.
    """

    __tablename__ = "ac_bot_app_grant_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(BigInteger)
    app_name: Mapped[str] = mapped_column(String(GRANT_APP_NAME_MAX_LENGTH))
    bot_id: Mapped[str] = mapped_column(String(GRANT_IDENTITY_MAX_LENGTH))
    user_id: Mapped[str] = mapped_column(String(GRANT_IDENTITY_MAX_LENGTH))
    owner_id: Mapped[str] = mapped_column(String(GRANT_IDENTITY_MAX_LENGTH))
    action: Mapped[str] = mapped_column(String(32))
    env: Mapped[str] = mapped_column(String(GRANT_ENV_MAX_LENGTH))
    avernet_tenant: Mapped[str] = mapped_column(String(64), server_default="teamclaw")
    gmt_create: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        Index(
            "idx_bot_app_grant_log_bot",
            "avernet_tenant",
            "bot_id",
            "owner_id",
            "env",
            "gmt_create",
        ),
    )
