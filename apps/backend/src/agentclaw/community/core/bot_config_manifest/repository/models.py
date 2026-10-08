"""SQLAlchemy ORM model + business record for ``ac_bot_config_manifest``.

Registered on ``agentclaw.community.core.base.Base`` so the ``create_all``
bootstrap emits the table — the side-effect import lives in ``core/schema.py``.

Shaped after ``ac_bot_startup_script`` deliberately: the two tables answer the
same storage question — one optional caller-authored document per bot,
tenant-scoped, keyed on ``(env, entity_id, bot_id)``.

They differ on how that key is carried. ``ac_bot_startup_script`` hashes it into
a surrogate column because it kept ``entity_id`` at ``ac_bots``' 1024 characters,
which is 4096 utf8mb4 bytes and over InnoDB's 3072-byte index cap on its own.
Here the key is the columns themselves and ``entity_id`` is narrowed to 256 to
pay for it — see the column comments below for why 256, and the DDL for the
tenancy reasoning both tables share.
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
    # 256, not ``ac_bots.entity_id``'s 1024. This column is *in* the uniqueness
    # key, so its width is part of an index budget: InnoDB caps a key at 3072
    # bytes and utf8mb4 counts 4 bytes per character. At 256 the four key columns
    # come to 2384 bytes (tenant 256 + env 80 + entity_id 1024 + bot_id 1024),
    # which fits with room to spare; at 1024 ``entity_id`` alone would be 4096
    # and ``CREATE TABLE`` would be refused outright.
    #
    # 256 rather than the 64 that would also fit: an ``entity_id`` is a user id
    # copied from the bot record, and the platform's own validation admits up to
    # 1024 (``adapters/http/access/schemas.py``), so nothing enforces a short
    # one. Real values are short (``u_165137``), and 256 matches what the newer
    # tables here give a user id (``bot_collaborator.user_id``,
    # ``access.user_id``, ``task.owner_user_id``). The headroom costs 688 bytes
    # of a budget with 688 to spare and buys the guarantee that a legacy long
    # entity_id cannot fail a manifest write.
    entity_id = Column(
        String(256), nullable=False, comment="实体ID（bot 的 entity_id）"
    )
    bot_id = Column(String(256), nullable=False, comment="Bot ID")
    # The caller's bytes, not a re-serialisation: ``script.body`` is a shell
    # body and a YAML round trip preserves its value, not its bytes.
    #
    # ``MEDIUMTEXT`` on MySQL, and that variant is load-bearing rather than
    # decorative. Plain ``Text`` renders as MySQL ``TEXT``, which holds 65,535
    # bytes — one *less* than ``MAX_DOCUMENT_BYTES`` (65,536), and the size check
    # is a strict ``>``, so a document of exactly 64 KiB passes validation. On a
    # ``TEXT`` column that accepted write then either fails outright (strict SQL
    # mode) or silently truncates (non-strict): the byte-exact "stored verbatim"
    # guarantee broken for a document the API itself called valid.
    #
    # It is reachable because ``create_schema`` defaults to ``True``
    # (``di/config_community.py``), so a deployment that lets the app emit its
    # own DDL gets this column rather than the out-of-band prod DDL — which
    # already says ``mediumtext``. The variant is what makes the two agree.
    #
    # ``ac_bot_startup_script.script`` has the same divergence and is dormant
    # there: its 24 KiB cap sits far under ``TEXT``. Here the limit lands exactly
    # on top of it.
    document = Column(
        Text().with_variant(mysql.MEDIUMTEXT(), "mysql"),
        nullable=False,
        comment="配置清单文档原文（清空即删行）",
    )
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

    # The logical key, carried directly rather than through a digest. Every read
    # filters on exactly these columns, so the table has one index and it is the
    # one the lookups use.
    __table_args__ = (
        UniqueConstraint(
            "avernet_tenant",
            "env",
            "entity_id",
            "bot_id",
            name="uk_tenant_env_entity_bot",
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
