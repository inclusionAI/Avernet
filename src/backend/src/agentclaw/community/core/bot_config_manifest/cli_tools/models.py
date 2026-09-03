"""SQLAlchemy ORM model + business record for ``ac_bot_cli_tool`` (W9).

The platform's record of which command-line tools a bot has installed. It is
the answer to "what does this bot have", and it is what makes replacement and
removal decidable: a manifest apply's full override computes its removals from
these rows, not from whatever an engine happens to report.

**No column holds a container path.** Where a tool lands is the engine's
decision — every operation addresses a tool by ``name`` — so nothing here
records, validates or leaks a filesystem location. ``oss_key`` is the
*platform's own* object key, not a container path.

Registered on ``agentclaw.community.core.base.Base`` so the local-mode
``create_all`` bootstrap emits the table; the side-effect import lives in
``core/schema.py``.
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
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.utils.avernet_tenant_guard import (
    register_avernet_tenant_guard,
)
from agentclaw.community.utils.env_utils import get_current_env

# SQLite only auto-increments columns declared as exactly "INTEGER PRIMARY KEY".
# BigInteger renders as "BIGINT" in SQLite, which breaks autoincrement.
#
# Declared locally rather than imported from ``plugin_api.models``, matching all
# four sibling model modules in this package. The canonical one lives across a
# module boundary this package does not declare, and widening
# ``internal_dependencies`` for a two-line constant would trade an enforced
# architectural rule for a de-duplication — ``test_declared_deps_cover_actual_imports``
# is what makes that a real choice rather than a preference.
AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")

#: Who installed a tool, when it was not a user. Stored in ``installed_by``
#: beside real user ids, so a full override can say what it replaced rather
#: than silently overwriting an API-installed tool.
INSTALLED_BY_MANIFEST = "manifest"


class BotCliToolRecord(BaseModel):
    """CLI 工具业务模型（对应 ac_bot_cli_tool 表）。"""

    id: Optional[int] = Field(default=None, description="主键ID")
    env: str = Field(default="dev", description="环境标识")
    entity_id: str = Field(..., description="实体ID（bot 的 entity_id）")
    bot_id: str = Field(..., description="Bot ID")
    name: str = Field(..., description="命令名，bot 内唯一")
    source: str = Field(..., description="声明的来源 URL / 命名源")
    digest: str = Field(..., description="用户钉扎的 sha256:…")
    subpath: Optional[str] = Field(default=None, description="归档内选中的成员")
    md5: str = Field(..., description="平台对最终文件计算的 MD5")
    size_bytes: int = Field(..., description="最终文件字节数")
    version: Optional[str] = Field(default=None, description="元数据，不参与收敛")
    oss_key: str = Field(..., description="平台保存字节的对象键")
    installed_by: str = Field(..., description="'manifest' 或用户 ID")
    modifier: str = Field(..., description="最后写入者")
    gmt_create: datetime = Field(default_factory=datetime.now, description="创建时间")
    gmt_modified: datetime = Field(default_factory=datetime.now, description="修改时间")

    @property
    def convergence_key(self) -> tuple[str, Optional[str]]:
        """What decides whether a declaration is already installed.

        ``digest`` **and** ``subpath`` together, never ``digest`` alone: the
        same archive with ``subpath`` moved from ``bin/old`` to ``bin/new``
        delivers a *different file* under the same command name, and keying on
        the digest would report it unchanged and leave the old binary answering
        to a name whose declaration now means the new one.

        ``version`` is deliberately absent — bumping a version string alone
        must not force a redelivery.
        """
        return (self.digest, self.subpath)


class BotCliToolModel(Base):
    """SQLAlchemy ORM model for the ``ac_bot_cli_tool`` table."""

    __tablename__ = "ac_bot_cli_tool"

    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )

    env = Column(
        String(20),
        nullable=False,
        default=get_current_env,
        comment="环境标识: prod/pre/dev",
    )
    # 1024, matching ``ac_bots.entity_id`` exactly — the same reasoning
    # ``ac_bot_startup_script`` records: narrowing it to fit an index key would
    # trade an index problem for a data-truncation one. The key is bounded by
    # ``tool_key`` below instead.
    entity_id = Column(
        String(1024), nullable=False, comment="实体ID（bot 的 entity_id）"
    )
    bot_id = Column(String(256), nullable=False, comment="Bot ID")
    name = Column(String(128), nullable=False, comment="命令名（bot 内唯一）")

    source = Column(Text, nullable=False, comment="声明的来源 URL / 命名源")
    digest = Column(String(80), nullable=False, comment="用户钉扎的 sha256:…")
    subpath = Column(String(512), nullable=True, comment="归档内选中的成员路径")
    md5 = Column(String(32), nullable=False, comment="平台对最终文件计算的 MD5")
    size_bytes = Column(BigInteger, nullable=False, comment="最终文件字节数")
    version = Column(String(64), nullable=True, comment="元数据，不参与收敛")
    # The platform's own object key — where *we* kept the bytes. Not a
    # container path: the engine owns placement and never tells us where.
    oss_key = Column(String(512), nullable=False, comment="平台保存字节的对象键")
    # 1024, matching ``modifier`` — both hold the same acting-user principal,
    # so a width that fits one must fit the other. A narrower ``installed_by``
    # would fail the whole upsert on a long principal *after* the bytes are in
    # OSS and the tool is already installed, leaving no row for a live tool.
    installed_by = Column(
        String(1024), nullable=False, comment="'manifest' 或安装它的用户 ID"
    )
    modifier = Column(String(1024), nullable=False, comment="审计：最后写入者")

    # Data-isolation tenant, load-bearing for the same reason
    # ``ac_bot_startup_script`` documents: ``ac_bots`` is itself tenant-guarded,
    # so a bot_id is unique only *within* a tenant. Without this column two
    # tenants whose (entity_id, bot_id) collide would share tool rows — and a
    # tool row names an executable that runs in a container.
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")

    #: Bounded surrogate for the uniqueness key: sha256 of the logical key.
    #:
    #: The logical key is (env, entity_id, bot_id, name), and ``entity_id``
    #: alone is 1024 utf8mb4 characters — 4096 bytes, past InnoDB's 3072-byte
    #: index-key cap on its own. Indexing a fixed-width digest keeps the wide
    #: columns at their true widths while the constraint stays enforceable.
    #:
    #: Written by the repository, never by a caller.
    tool_key = Column(
        String(64),
        nullable=False,
        comment="唯一键代理：sha256(env|entity_id|bot_id|name)",
    )

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
        # A command name is unwritable twice for one bot, not merely refused by
        # a validator: two concurrent installs of the same name cannot both
        # land, whichever order they arrive in.
        UniqueConstraint(
            "avernet_tenant",
            "tool_key",
            name="uk_tenant_cli_tool_key",
        ),
        # Declared here, under the DDL's own name, rather than as ``index=True``
        # on a column. A column-level flag would emit ``ix_ac_bot_cli_tool_bot_id``
        # under ``create_all`` while prod's hand-applied DDL builds a different
        # index — the two schemas this one repository body runs against would
        # then have different indexes, and a plan validated locally would not
        # describe prod. ``ac_bot_startup_script``'s DDL states the rule:
        # no second lookup key that can drift out of step with the ORM model.
        Index(
            "idx_tenant_env_bot",
            "avernet_tenant",
            "env",
            "bot_id",
        ),
    )

    def to_record(self) -> BotCliToolRecord:
        """Convert the ORM row to its business record."""
        return BotCliToolRecord(
            id=self.id,
            env=self.env,
            entity_id=self.entity_id,
            bot_id=self.bot_id,
            name=self.name,
            source=self.source,
            digest=self.digest,
            subpath=self.subpath,
            md5=self.md5,
            size_bytes=self.size_bytes,
            version=self.version,
            oss_key=self.oss_key,
            installed_by=self.installed_by,
            modifier=self.modifier,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


register_avernet_tenant_guard(BotCliToolModel)


__all__ = [
    "INSTALLED_BY_MANIFEST",
    "BotCliToolModel",
    "BotCliToolRecord",
]
