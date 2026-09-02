"""ORM model + business record for ``ac_bot_config_managed_files`` (W8, #1476).

The **index** half of the platform's own copy of what a manifest delivered to a
teclaw bot: one row per file the platform put into the bot-data object store on
a manifest's behalf. The bytes are in the store; this table says which files
exist, where, with what digest, and which apply wrote them. The teclaw composer
reads it to put ``{store, path}`` refs into the artifact, and the store-backed
materialisers converge it (an unchanged digest writes nothing).

A row is a *delivered file*, not a fetch event — that is W11's
``ac_manifest_content``. Under §3.2's overwrite a category's rows are exactly
its area; a category the manifest does not declare has no rows and is left to
the engine.

Keyed like ``ac_bot_config_manifest`` — tenant, env, entity_id, bot_id — plus
the category and the file's engine-relative path. The path is too wide for the
index budget (InnoDB caps a key at 3072 bytes, utf8mb4 counts four per
character), so uniqueness hashes it: ``path_hash`` is sha256 of ``rel_path``,
the way ``ac_bot_startup_script`` hashed its key. The readable path is stored
beside it.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.utils.avernet_tenant_guard import (
    register_avernet_tenant_guard,
)
from agentclaw.community.utils.env_utils import get_current_env

AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")


def managed_path_hash(rel_path: str) -> str:
    """The uniqueness surrogate for a path: sha256 hex of its UTF-8 bytes."""
    return hashlib.sha256(rel_path.encode("utf-8")).hexdigest()


class ManagedFileRecord(BaseModel):
    """平台托管文件索引业务模型（对应 ac_bot_config_managed_files 表）。"""

    id: Optional[int] = Field(default=None, description="主键ID")
    env: str = Field(default="dev", description="环境标识")
    entity_id: str = Field(..., description="实体ID（bot 的 entity_id）")
    bot_id: str = Field(..., description="Bot ID")
    category: str = Field(..., description="类目：identity / resources / skills")
    name: str = Field(..., description="条目名：identity 文件类型 / 资源路径 / skill 名")
    rel_path: str = Field(..., description="引擎相对路径，如 identity/RULES.md")
    store_key: str = Field(..., description="bot-data store 内的对象键")
    digest: str = Field(..., description="sha256:<hex>")
    size_bytes: int = Field(..., description="字节数")
    apply_id: Optional[str] = Field(default=None, description="写入它的 apply")
    gmt_create: datetime = Field(default_factory=datetime.now, description="创建时间")
    gmt_modified: datetime = Field(default_factory=datetime.now, description="修改时间")


class BotConfigManagedFileModel(Base):
    """SQLAlchemy ORM model for ``ac_bot_config_managed_files``."""

    __tablename__ = "ac_bot_config_managed_files"

    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    env = Column(
        String(20), nullable=False, default=get_current_env, comment="环境标识: prod/pre/dev"
    )
    # 256 for the same index-budget reason ``ac_bot_config_manifest`` gives.
    entity_id = Column(String(256), nullable=False, comment="实体ID（bot 的 entity_id）")
    bot_id = Column(String(256), nullable=False, comment="Bot ID")
    category = Column(String(32), nullable=False, comment="类目: identity/resources/skills")
    name = Column(String(512), nullable=False, comment="条目名")
    # Readable, and NOT in the unique key: 768 characters is 3072 bytes on its
    # own. ``path_hash`` stands in for it there.
    rel_path = Column(String(768), nullable=False, comment="引擎相对路径")
    path_hash = Column(String(64), nullable=False, comment="sha256(rel_path)，唯一键代理")
    store_key = Column(String(1024), nullable=False, comment="bot-data store 对象键")
    digest = Column(String(80), nullable=False, comment="sha256:<hex>")
    size_bytes = Column(Integer, nullable=False, comment="字节数")
    apply_id = Column(String(64), nullable=True, comment="写入它的 apply_id")

    # Data-isolation tenant — load-bearing here for the reason the manifest
    # table gives: a bot_id is unique only within a tenant.
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")

    gmt_create = Column(DateTime, default=func.now(), nullable=False, comment="创建时间")
    gmt_modified = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False, comment="修改时间"
    )

    # One index, and every read is a prefix of it: a category listing filters on
    # the first five columns, a single-file lookup on all six. 64+20+256+256+32+64
    # characters = 2768 utf8mb4 bytes, inside the 3072 cap.
    __table_args__ = (
        UniqueConstraint(
            "avernet_tenant",
            "env",
            "entity_id",
            "bot_id",
            "category",
            "path_hash",
            name="uk_tenant_env_entity_bot_category_path",
        ),
    )

    def to_record(self) -> ManagedFileRecord:
        return ManagedFileRecord(
            id=self.id,
            env=self.env,
            entity_id=self.entity_id,
            bot_id=self.bot_id,
            category=self.category,
            name=self.name,
            rel_path=self.rel_path,
            store_key=self.store_key,
            digest=self.digest,
            size_bytes=self.size_bytes,
            apply_id=self.apply_id,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


register_avernet_tenant_guard(BotConfigManagedFileModel)
