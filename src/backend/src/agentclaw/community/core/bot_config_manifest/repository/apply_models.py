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
    """一次 apply 的记录（对应 ac_bot_config_manifest_apply 表）。"""

    id: Optional[int] = Field(default=None, description="主键ID")
    apply_id: str = Field(..., description="本次 apply 的公开标识")
    env: str = Field(default="dev", description="环境标识")
    entity_id: str = Field(..., description="实体ID（bot 的 entity_id）")
    bot_id: str = Field(..., description="Bot ID")
    trigger: str = Field(..., description="触发来源：explicit/create/republish/restart")
    status: str = Field(..., description="RUNNING 或三个终态之一")
    report: str = Field(..., description="逐条明细（JSON）")
    actor: str = Field(..., description="审计：发起者")
    started_at: datetime = Field(..., description="开始时间")
    finished_at: Optional[datetime] = Field(
        default=None, description="结束时间；RUNNING 期间为空"
    )
    gmt_create: datetime = Field(default_factory=datetime.now, description="创建时间")
    gmt_modified: datetime = Field(default_factory=datetime.now, description="修改时间")


class BotConfigManifestApplyLockRecord(BaseModel):
    """apply 串行锁（对应 ac_bot_config_manifest_apply_lock 表）。"""

    id: Optional[int] = Field(default=None, description="主键ID")
    env: str = Field(default="dev", description="环境标识")
    entity_id: str = Field(..., description="实体ID（bot 的 entity_id）")
    bot_id: str = Field(..., description="Bot ID")
    holder_user_id: str = Field(..., description="持锁者（发起 apply 的人）")
    lock_token: str = Field(..., description="持锁令牌（fencing token，释放时比对）")
    gmt_create: datetime = Field(default_factory=datetime.now, description="创建时间")
    gmt_modified: datetime = Field(default_factory=datetime.now, description="修改时间")


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
    apply_id = Column(String(64), nullable=False, comment="本次 apply 的公开标识")

    env = Column(
        String(20),
        nullable=False,
        default=get_current_env,
        comment="环境标识: prod/pre/dev",
    )
    # 256 for the index-budget reason ``ac_bot_config_manifest`` records: this
    # column is in both indexes below, InnoDB caps a key at 3072 bytes, and
    # utf8mb4 counts 4 bytes per character. ``ac_bots.entity_id`` is 1024, which
    # would be 4096 bytes on its own.
    entity_id = Column(
        String(256), nullable=False, comment="实体ID（bot 的 entity_id）"
    )
    bot_id = Column(String(256), nullable=False, comment="Bot ID")

    # ``explicit`` is the only value this wave writes — W4's single entry point
    # is the explicit POST. W8 adds republish/restart and W13 adds create, and
    # neither needs a migration to do it.
    trigger = Column(
        String(32), nullable=False, comment="触发来源：explicit/create/republish/restart"
    )
    # RUNNING on insert, terminal on completion — the two-write lifecycle apply's
    # async shape requires. Denormalised out of ``report`` so "show me failed
    # applies" is a query rather than a scan of JSON, and so a poll is one
    # indexed read.
    status = Column(String(16), nullable=False, comment="RUNNING 或三个终态之一")
    # ``MEDIUMTEXT`` on MySQL for the reason ``ac_bot_config_manifest.document``
    # records: plain ``Text`` renders as MySQL ``TEXT`` at 65,535 bytes, and a
    # report over a large manifest has no such cap to lean on.
    report = Column(
        Text().with_variant(mysql.MEDIUMTEXT(), "mysql"),
        nullable=False,
        comment="逐条明细（JSON）",
    )
    # Bounded the way ``MAX_MODIFIER_CHARS`` bounds the manifest's ``modifier``,
    # and for the same reason: an application actor composes a prefix onto a
    # 1024-character user id, so the composed value can legitimately exceed any
    # narrower width without anything being malformed.
    actor = Column(String(1024), nullable=False, comment="审计：发起者")

    started_at = Column(DateTime, nullable=False, comment="开始时间")
    # Null exactly while ``status`` is RUNNING. The two move together.
    finished_at = Column(DateTime, nullable=True, comment="结束时间；RUNNING 期间为空")

    # Data-isolation tenant. Load-bearing for the reason the manifest table
    # records: ``ac_bots`` is itself tenant-guarded, so a bot_id is unique only
    # within a tenant.
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")

    gmt_create = Column(
        DateTime, default=func.now(), nullable=False, comment="创建时间"
    )
    gmt_modified = Column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="修改时间",
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
        comment="环境标识: prod/pre/dev",
    )
    entity_id = Column(
        String(256), nullable=False, comment="实体ID（bot 的 entity_id）"
    )
    bot_id = Column(String(256), nullable=False, comment="Bot ID")
    holder_user_id = Column(
        String(1024), nullable=False, comment="持锁者（发起 apply 的人）"
    )
    lock_token = Column(
        String(256), nullable=False, comment="持锁令牌（fencing token，释放时比对）"
    )

    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")

    gmt_create = Column(
        DateTime, default=func.now(), nullable=False, comment="创建时间"
    )
    gmt_modified = Column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="修改时间",
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
