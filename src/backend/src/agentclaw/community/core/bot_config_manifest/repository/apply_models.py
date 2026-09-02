"""ORM models + business records for the apply record and its serialization lock.

Two tables, registered on ``core.base.Base`` so the ``create_all`` bootstrap
emits them — the side-effect import lives in ``core/schema.py``.

``ac_bot_config_manifest_apply`` is the apply record work-items §2.3 asks for:
the manifest module's own account of what it materialised, kept for audit and
for ``keep_last``, and putting **no mark on any materialised entity** — a
manifest-created MCP activation is stored identically to a hand-made one and
nothing downstream can tell them apart.

``ac_bot_config_manifest_apply_lock`` serialises applies against one bot. It is
a **separate table** from ``ac_bot_restart_lock`` rather than a second row in
it: applying a manifest and restarting a bot are different operations, and
sharing a row would make a restart block an apply as an accident of storage
rather than as a decision. The *pattern* is reused verbatim, which is what
work-items §5 asks for.

Both tables carry the same logical key and the same column widths as
``ac_bot_config_manifest``, for the index-budget reason recorded there and in
the DDL: InnoDB caps a key at 3072 bytes, utf8mb4 counts 4 bytes per character,
so ``entity_id`` is 256 rather than ``ac_bots``' 1024.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.utils.avernet_tenant_guard import (
    register_avernet_tenant_guard,
)
from agentclaw.community.utils.env_utils import get_current_env

# SQLite only auto-increments columns declared as exactly "INTEGER PRIMARY KEY".
# BigInteger renders as "BIGINT" in SQLite, which breaks autoincrement.
AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")


class BotConfigManifestApplyRecord(BaseModel):
    """One apply's record — the ``ac_bot_config_manifest_apply`` row."""

    id: Optional[int] = Field(default=None, description="Primary key")
    apply_id: str = Field(..., description="This apply's public handle")
    env: str = Field(default="dev", description="Environment")
    entity_id: str = Field(..., description="Entity id (the bot's entity_id)")
    bot_id: str = Field(..., description="Bot ID")
    trigger: str = Field(..., description="What started it: explicit/put/create:pre_container/create:on_container")
    status: str = Field(..., description="RUNNING, or one of the three terminal statuses")
    report: str = Field(..., description="The per-entry report (JSON)")
    actor: str = Field(..., description="Audit: who started it")
    started_at: datetime = Field(..., description="When the apply began")
    finished_at: Optional[datetime] = Field(
        default=None, description="When it ended; null while RUNNING"
    )
    gmt_create: datetime = Field(default_factory=datetime.now, description="Row created")
    gmt_modified: datetime = Field(default_factory=datetime.now, description="Row last modified")


class BotConfigManifestApplyLockRecord(BaseModel):
    """The apply serialization lock — the ``ac_bot_config_manifest_apply_lock`` row."""

    id: Optional[int] = Field(default=None, description="Primary key")
    env: str = Field(default="dev", description="Environment")
    entity_id: str = Field(..., description="Entity id (the bot's entity_id)")
    bot_id: str = Field(..., description="Bot ID")
    holder_user_id: str = Field(..., description="Lock holder (whoever started the apply)")
    lock_token: str = Field(..., description="Fencing token, compared on release")
    gmt_create: datetime = Field(default_factory=datetime.now, description="Row created")
    gmt_modified: datetime = Field(default_factory=datetime.now, description="Row last modified")


class BotConfigManifestApplyModel(Base):
    """SQLAlchemy ORM model for ``ac_bot_config_manifest_apply``."""

    __tablename__ = "ac_bot_config_manifest_apply"

    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )

    # The report's public identity. Returned by ``start_apply`` and polled on;
    # also what a per-entry table would join on, if provenance ever needs one.
    #
    # NOT a lookup key on its own: every read filters on the bot key as well, so
    # an id guessed or leaked from another bot resolves to nothing. The id is a
    # handle, never the authorization.
    apply_id = Column(String(64), nullable=False, comment="This apply's public handle")

    env = Column(
        String(20),
        nullable=False,
        default=get_current_env,
        comment="Environment: prod/pre/dev",
    )
    # 256 for the index-budget reason ``ac_bot_config_manifest`` records: this
    # column is in both indexes below, InnoDB caps a key at 3072 bytes, and
    # utf8mb4 counts 4 bytes per character. ``ac_bots.entity_id`` is 1024, which
    # would be 4096 bytes on its own.
    entity_id = Column(
        String(256), nullable=False, comment="Entity id (the bot's entity_id)"
    )
    bot_id = Column(String(256), nullable=False, comment="Bot ID")

    # The vocabulary is ``apply/triggers.py``: ``explicit`` (W4), W13's two
    # ``create:*`` phases, and ``put`` (W8). Restart and republish were deferred
    # (W8 spec D-1). None of them needed a migration; every value fits.
    trigger = Column(
        String(32), nullable=False, comment="What started it: explicit/put/create:pre_container/create:on_container"
    )
    # RUNNING on insert, terminal on completion — the two-write lifecycle apply's
    # async shape requires. Denormalised out of ``report`` so "show me failed
    # applies" is a query rather than a scan of JSON, and so a poll is one
    # indexed read.
    status = Column(String(16), nullable=False, comment="RUNNING, or one of the three terminal statuses")
    # ``MEDIUMTEXT`` on MySQL for the reason ``ac_bot_config_manifest.document``
    # records: plain ``Text`` renders as MySQL ``TEXT`` at 65,535 bytes, and a
    # report over a large manifest has no such cap to lean on.
    report = Column(
        Text().with_variant(mysql.MEDIUMTEXT(), "mysql"),
        nullable=False,
        comment="The per-entry report (JSON)",
    )
    # Bounded the way ``MAX_MODIFIER_CHARS`` bounds the manifest's ``modifier``,
    # and for the same reason: an application actor composes a prefix onto a
    # 1024-character user id, so the composed value can legitimately exceed any
    # narrower width without anything being malformed.
    actor = Column(String(1024), nullable=False, comment="Audit: who started it")

    started_at = Column(DateTime, nullable=False, comment="When the apply began")
    # Null exactly while ``status`` is RUNNING. The two move together.
    finished_at = Column(DateTime, nullable=True, comment="When it ended; null while RUNNING")

    # Data-isolation tenant. Load-bearing for the reason the manifest table
    # records: ``ac_bots`` is itself tenant-guarded, so a bot_id is unique only
    # within a tenant.
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")

    gmt_create = Column(
        DateTime, default=func.now(), nullable=False, comment="Row created"
    )
    gmt_modified = Column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="Row last modified",
    )

    # Two indexes, one per read. There are exactly two reads and no others.
    __table_args__ = (
        # ``GET …/last-apply`` — the newest row for this bot.
        Index(
            "idx_manifest_apply_latest",
            "avernet_tenant",
            "env",
            "entity_id",
            "bot_id",
            "id",
        ),
        # ``GET …/applies/{apply_id}`` — the poll by id. Carries the bot key
        # rather than being a bare ``apply_id`` lookup, so an id from another
        # bot cannot resolve here. The id is a handle, not an authorization.
        Index(
            "idx_manifest_apply_by_id",
            "avernet_tenant",
            "env",
            "entity_id",
            "bot_id",
            "apply_id",
        ),
    )

    def to_record(self) -> BotConfigManifestApplyRecord:
        """Convert the ORM row to its business record."""
        return BotConfigManifestApplyRecord(
            id=self.id,
            apply_id=self.apply_id,
            env=self.env,
            entity_id=self.entity_id,
            bot_id=self.bot_id,
            trigger=self.trigger,
            status=self.status,
            report=self.report,
            actor=self.actor,
            started_at=self.started_at,
            finished_at=self.finished_at,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


class BotConfigManifestApplyLockModel(Base):
    """SQLAlchemy ORM model for ``ac_bot_config_manifest_apply_lock``.

    Shaped after ``ac_bot_restart_lock`` deliberately — the UNIQUE constraint
    **is** the lock, and the database arbitrates concurrent inserts. What
    differs is only the key's tenant column, which this feature's tables carry
    throughout.
    """

    __tablename__ = "ac_bot_config_manifest_apply_lock"

    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )

    env = Column(
        String(20),
        nullable=False,
        default=get_current_env,
        comment="Environment: prod/pre/dev",
    )
    entity_id = Column(
        String(256), nullable=False, comment="Entity id (the bot's entity_id)"
    )
    bot_id = Column(String(256), nullable=False, comment="Bot ID")
    holder_user_id = Column(
        String(1024), nullable=False, comment="Lock holder (whoever started the apply)"
    )
    lock_token = Column(
        String(256), nullable=False, comment="Fencing token, compared on release"
    )

    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")

    gmt_create = Column(
        DateTime, default=func.now(), nullable=False, comment="Row created"
    )
    gmt_modified = Column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="Row last modified",
    )

    __table_args__ = (
        # The UNIQUE constraint IS the lock: one row per bot, and the database
        # decides which concurrent INSERT wins.
        UniqueConstraint(
            "avernet_tenant",
            "env",
            "entity_id",
            "bot_id",
            name="uk_manifest_apply_lock",
        ),
    )

    def to_record(self) -> BotConfigManifestApplyLockRecord:
        """Convert the ORM row to its business record."""
        return BotConfigManifestApplyLockRecord(
            id=self.id,
            env=self.env,
            entity_id=self.entity_id,
            bot_id=self.bot_id,
            holder_user_id=self.holder_user_id,
            lock_token=self.lock_token,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


# Confines every read to the request's tenant and stamps it on insert. The
# registrar validates that the mapped column exists — see
# ``utils/avernet_tenant_guard`` for why that check is what stands between a bad
# declaration here and a cross-tenant read.
register_avernet_tenant_guard(BotConfigManifestApplyModel)
register_avernet_tenant_guard(BotConfigManifestApplyLockModel)
