"""SQLAlchemy ORM model + business record for ``ac_bot_config_manifest``.

Registered on ``agentclaw.community.core.base.Base`` so the ``create_all``
bootstrap emits the table — the side-effect import lives in ``core/schema.py``.

Shaped after ``ac_bot_startup_script`` deliberately, down to the surrogate key:
the two tables answer the same storage question (one optional caller-authored
document per bot, tenant-scoped, keyed on a logical triple too wide to index).
Where they differ the difference is in the SQL comments, not here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
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
AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")


class BotConfigManifestRecord(BaseModel):
    """配置清单业务模型（对应 ac_bot_config_manifest 表）。"""

    id: Optional[int] = Field(default=None, description="主键ID")
    env: str = Field(default="dev", description="环境标识")
    entity_id: str = Field(..., description="实体ID（bot 的 entity_id）")
    bot_id: str = Field(..., description="Bot ID")
    document: str = Field(..., description="配置清单文档原文")
    size_bytes: int = Field(..., description="文档字节数（UTF-8）")
    schema_version: int = Field(..., description="schema 版本")
    modifier: str = Field(..., description="最后写入者")
    gmt_create: datetime = Field(default_factory=datetime.now, description="创建时间")
    gmt_modified: datetime = Field(default_factory=datetime.now, description="修改时间")


class BotConfigManifestModel(Base):
    """SQLAlchemy ORM model for the ``ac_bot_config_manifest`` table."""

    __tablename__ = "ac_bot_config_manifest"

    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )

    env = Column(
        String(20),
        nullable=False,
        default=get_current_env,
        comment="环境标识: prod/pre/dev",
    )
    # 1024, matching ``ac_bots.entity_id`` exactly. The uniqueness key is
    # carried by ``manifest_key`` below, so this column is free to match its
    # source instead of being narrowed to fit an index.
    entity_id = Column(
        String(1024), nullable=False, comment="实体ID（bot 的 entity_id）"
    )
    bot_id = Column(String(256), nullable=False, comment="Bot ID")
    # The caller's bytes, not a re-serialisation: ``script.body`` is a shell
    # body and a YAML round trip preserves its value, not its bytes.
    document = Column(Text, nullable=False, comment="配置清单文档原文（清空即删行）")
    size_bytes = Column(Integer, nullable=False, comment="文档字节数（UTF-8）")
    schema_version = Column(Integer, nullable=False, comment="schema 版本")
    modifier = Column(String(1024), nullable=False, comment="审计：最后写入者")

    # Data-isolation tenant. Load-bearing, not boilerplate — ``ac_bots`` is
    # itself tenant-guarded, so a bot_id is unique only *within* a tenant, and
    # two colliding legacy "default" bots would otherwise share one manifest
    # row. See the DDL for what that would mean once apply lands.
    #
    # server_default (not a Python default=) so create_all emits the same
    # DEFAULT 'teamclaw' the out-of-band prod DDL applies; the context-aware
    # value on ORM inserts comes from the before_insert guard registered below.
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")

    #: Bounded surrogate for the uniqueness key: sha256 of (env, entity_id,
    #: bot_id). ``entity_id`` alone is 4096 utf8mb4 bytes, past InnoDB's
    #: 3072-byte index-key cap. Written by the repository, never by a caller.
    manifest_key = Column(
        String(64), nullable=False, comment="唯一键代理：sha256(env|entity_id|bot_id)"
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
        UniqueConstraint(
            "avernet_tenant",
            "manifest_key",
            name="uk_tenant_manifest_key",
        ),
    )

    def to_record(self) -> BotConfigManifestRecord:
        """Convert the ORM row to its business record."""
        return BotConfigManifestRecord(
            id=self.id,
            env=self.env,
            entity_id=self.entity_id,
            bot_id=self.bot_id,
            document=self.document,
            size_bytes=self.size_bytes,
            schema_version=self.schema_version,
            modifier=self.modifier,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


# Confines every read to the request's tenant and stamps it on insert. The
# registrar validates that the mapped column exists — see
# ``utils/avernet_tenant_guard`` for why that check is the thing standing
# between a bad declaration here and a cross-tenant read.
register_avernet_tenant_guard(BotConfigManifestModel)
