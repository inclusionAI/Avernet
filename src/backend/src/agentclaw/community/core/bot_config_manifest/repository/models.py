"""SQLAlchemy ORM model + business record for ``ac_bot_config_manifest``.

Registered on ``agentclaw.community.core.base.Base``; the eager import that
makes ``create_all`` emit the table lives in ``core/schema.py``
``import_all_models``.
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
    """Bot 配置清单业务记录（对应 ac_bot_config_manifest 表）。

    ``document`` 是服务层对解析后文档的规范序列化。仓储只透传不重序列化：
    JSON 字符串值（script 正文尤其）必须逐字节往返一致，显式声明的空类目
    必须与缺省保持可区分（D2 语义），任何存储期重排都会以不可见的方式
    破坏这两条契约。
    """

    id: Optional[int] = Field(default=None, description="主键ID")
    env: str = Field(default="dev", description="环境标识")
    entity_id: str = Field(..., description="实体ID（bot 的 entity_id）")
    bot_id: str = Field(..., description="Bot ID")
    schema_version: int = Field(..., description="manifest schema 版本")
    document: str = Field(..., description="配置清单文档 JSON 规范形态")
    size_bytes: int = Field(..., description="文档字节数（UTF-8）")
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
    # 1024, matching ``ac_bots.entity_id`` exactly — not narrowed, for the same
    # reason as ac_bot_startup_script: the key is bounded by ``manifest_key``
    # below, so this column is free to match its source.
    entity_id = Column(
        String(1024), nullable=False, comment="实体ID（bot 的 entity_id）"
    )
    bot_id = Column(String(256), nullable=False, comment="Bot ID")
    schema_version = Column(Integer, nullable=False, comment="manifest schema 版本（v1=1）")
    document = Column(
        Text, nullable=False, comment="配置清单文档 JSON 规范形态（整份替换，保空类目声明）"
    )
    size_bytes = Column(Integer, nullable=False, comment="文档字节数（UTF-8）")
    modifier = Column(String(1024), nullable=False, comment="审计：最后写入者")

    # Data-isolation tenant; see the DDL for the collision reasoning. The
    # before_insert guard registered below keeps every insert context-aware.
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")

    #: Bounded surrogate for the uniqueness key — sha256 over the
    #: length-prefixed logical key. Written by the repository, never by a
    #: caller. The tenant is carried alongside rather than hashed in, so the
    #: isolation boundary stays visible in the key itself.
    manifest_key = Column(
        String(64),
        nullable=False,
        comment="唯一键代理：sha256(长度前缀 env/entity_id/bot_id)",
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
            schema_version=self.schema_version,
            document=self.document,
            size_bytes=self.size_bytes,
            modifier=self.modifier,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


# Confines every read to the request's tenant and stamps it on insert; the
# registrar validates that the mapped column exists.
register_avernet_tenant_guard(BotConfigManifestModel)
