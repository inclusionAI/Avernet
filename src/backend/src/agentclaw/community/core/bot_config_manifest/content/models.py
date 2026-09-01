"""SQLAlchemy ORM model + business records for ``ac_manifest_content``.

Registered on ``agentclaw.community.core.base.Base`` so the ``create_all``
bootstrap emits the table — the side-effect import lives in ``core/schema.py``.

Two halves, one mechanism (§2.8, W11):

- the BYTES live in the content-addressed blob directory owned by
  :class:`~agentclaw.community.core.bot_config_manifest.content.service.ManifestContentService`
  — never in this table, because schema §5 lets one entry be 100–200 MiB;
- this table is the append-only PROVENANCE LOG — one row per store event,
  carrying who fetched what from where, when, and under which credential
  *name*. No unique key: the same digest stored again is a new row, and that
  repetition is itself the audit fact. See the DDL for the full reasoning.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import (
    BigInteger,
    CHAR,
    Column,
    DateTime,
    Index,
    Integer,
    String,
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


@dataclass(frozen=True)
class ContentScope:
    """The bot a store event is on behalf of.

    Same axes as W1's manifest key, tenant aside: a ``bot_id`` is unique only
    within a tenant (ac_bots is itself tenant-scoped), and the tenant half of
    every filter arrives via the guard on the model, not through this value —
    callers pass the three axes a bot record already carries.
    """

    env: str
    entity_id: str
    bot_id: str


class StoredContentRecord(BaseModel):
    """溯源行业务模型（对应 ac_manifest_content 表）。"""

    id: Optional[int] = Field(default=None, description="主键ID")
    env: str = Field(..., description="环境标识（必填，无默认——scope 之外不存在「默认环境」）")
    entity_id: str = Field(..., description="实体ID（存储键，非公开字段）")
    bot_id: str = Field(..., description="Bot ID")
    digest: str = Field(..., description="内容地址 sha256:<hex64>")
    source_url: str = Field(..., description="条目源 URL（去 userinfo/query）")
    fetched_url: str = Field(..., description="最终跳达 URL（去 userinfo/query）")
    credential_name: Optional[str] = Field(
        default=None, description="凭证名（仅名字，绝不存值）"
    )
    content_type: Optional[str] = Field(default=None, description="Content-Type")
    #: Advisory when populated at all: store() nulls an over-wide header
    #: rather than refusing the receipt (the digest, not the media type, is
    #: the reconciliation anchor).
    size_bytes: int = Field(..., description="字节数")
    fetched_at: datetime = Field(..., description="拉取时间")
    #: The apply and the entry this fetch served — nullable, see the DDL:
    #: rows written by anything that did not know them (keep_last reuse,
    #: hand-driven fetches) still answer "from where, for which bot, when",
    #: and the apply-linkage questions answer as NULL rather than as lies.
    apply_id: Optional[str] = Field(default=None, description="触发拉取的 apply 键")
    category: Optional[str] = Field(default=None, description="条目类目")
    entry_identity: Optional[str] = Field(default=None, description="条目标识")
    modifier: str = Field(default="", description="审计：触发拉取的身份")
    gmt_create: datetime = Field(default_factory=datetime.now, description="创建时间")
    gmt_modified: datetime = Field(default_factory=datetime.now, description="修改时间")


class ManifestContentModel(Base):
    """SQLAlchemy ORM model for the ``ac_manifest_content`` table."""

    __tablename__ = "ac_manifest_content"

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
    # The content address, same vocabulary as W2's FetchedObject.sha256.
    digest = Column(CHAR(71), nullable=False, comment="内容地址 sha256:<hex64>")
    source_url = Column(
        String(2048),
        nullable=False,
        comment="条目源 URL（${BOT_*} 替换后；去 userinfo/query）",
    )
    fetched_url = Column(
        String(2048), nullable=False, comment="最终跳达 URL（去 userinfo/query）"
    )
    # NAME only. The value is W3's ciphertext, and this table is an
    # append-only audit log — the one place a leaked secret could not even
    # be cleaned up (see the DDL's retention policy).
    credential_name = Column(
        String(128), nullable=True, comment="凭证名（仅名字；无凭证为 NULL）"
    )
    content_type = Column(String(256), nullable=True, comment="Content-Type")
    size_bytes = Column(BigInteger, nullable=False, comment="字节数")
    fetched_at = Column(DateTime, nullable=False, comment="拉取时间")
    # The link back to the apply record and the entry coordinates — added
    # with the table (see the DDL: never-update retention means a column
    # added after rows exist stays NULL forever for those rows).
    apply_id = Column(String(64), nullable=True, comment="触发拉取的 apply 键")
    category = Column(String(32), nullable=True, comment="条目类目")
    entry_identity = Column(String(256), nullable=True, comment="条目标识")
    modifier = Column(String(1024), nullable=False, default="", comment="审计：触发拉取的身份")

    # Data-isolation tenant — same load-bearing role as W1's table: a bot_id
    # is unique only within a tenant, and two colliding legacy "default" bots
    # must never read each other's receipts.
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
        # The audit query shape: one bot's receipts, newest first. No UNIQUE
        # key anywhere — the log is append-only and repetition is the fact.
        Index(
            "idx_tenant_env_entity_bot",
            "avernet_tenant",
            "env",
            "entity_id",
            "bot_id",
            "gmt_create",
        ),
        # "What did apply X fetch": the join the apply record's own comment
        # anticipated this table providing. (idx_digest was dropped with this
        # change: the repository exposes no digest-keyed read, and an index
        # with no reader taxes every append — it returns with the read's PR.)
        Index("idx_apply", "apply_id"),
    )

    def to_record(self) -> StoredContentRecord:
        """Convert the ORM row to its business record."""
        return StoredContentRecord(
            id=self.id,
            env=self.env,
            entity_id=self.entity_id,
            bot_id=self.bot_id,
            digest=self.digest,
            source_url=self.source_url,
            fetched_url=self.fetched_url,
            credential_name=self.credential_name,
            content_type=self.content_type,
            size_bytes=self.size_bytes,
            fetched_at=self.fetched_at,
            apply_id=self.apply_id,
            category=self.category,
            entry_identity=self.entry_identity,
            modifier=self.modifier,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


# Confines every read to the request's tenant and stamps it on insert.
register_avernet_tenant_guard(ManifestContentModel)
