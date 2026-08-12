"""SQLAlchemy ORM model + business record for ``ac_bot_startup_script``.

Registered on ``agentclaw.community.core.base.Base`` so the local-mode
``create_all`` bootstrap emits the table — the side-effect import lives in
``plugins/local/database.py``.
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


class BotStartupScriptRecord(BaseModel):
    """启动脚本业务模型（对应 ac_bot_startup_script 表）。"""

    id: Optional[int] = Field(default=None, description="主键ID")
    env: str = Field(default="dev", description="环境标识")
    entity_id: str = Field(..., description="实体ID（bot 的 entity_id）")
    bot_id: str = Field(..., description="Bot ID")
    script: str = Field(..., description="脚本正文")
    size_bytes: int = Field(..., description="脚本正文字节数（UTF-8）")
    modifier: str = Field(..., description="最后写入者")
    gmt_create: datetime = Field(default_factory=datetime.now, description="创建时间")
    gmt_modified: datetime = Field(default_factory=datetime.now, description="修改时间")


class BotStartupScriptModel(Base):
    """SQLAlchemy ORM model for the ``ac_bot_startup_script`` table."""

    __tablename__ = "ac_bot_startup_script"

    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )

    env = Column(
        String(20),
        nullable=False,
        default=get_current_env,
        comment="环境标识: prod/pre/dev",
    )
    # 1024, matching ``ac_bots.entity_id`` exactly. Narrowing it to fit the
    # uniqueness key was a mistake: a bot whose entity_id is legitimately
    # longer than the narrowed width could not have a script stored at all,
    # trading an index problem for a data-truncation one. The key is bounded by
    # ``script_key`` below instead, so this column is free to match its source.
    entity_id = Column(
        String(1024), nullable=False, comment="实体ID（bot 的 entity_id）"
    )
    bot_id = Column(String(256), nullable=False, comment="Bot ID")
    script = Column(Text, nullable=False, comment="脚本正文（清空即删行）")
    size_bytes = Column(Integer, nullable=False, comment="脚本正文字节数（UTF-8）")
    modifier = Column(String(1024), nullable=False, comment="审计：最后写入者")

    # Data-isolation tenant. Load-bearing, not boilerplate: ``ac_bots`` is
    # itself tenant-guarded, so a bot_id is unique *within* a tenant — legacy
    # "default" bots are documented as carrying residual cross-tenant collision
    # on their identifier (bots/router.py). Without this column two tenants
    # whose (entity_id, bot_id) collide would share one script row, so one
    # tenant could read or overwrite the other's script and have it execute in
    # the other's container on its next start.
    #
    # server_default (not a Python default=) so create_all emits the same
    # DEFAULT 'teamclaw' the out-of-band prod DDL applies; the context-aware
    # value on ORM inserts comes from the before_insert guard registered below.
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")

    #: Bounded surrogate for the uniqueness key: sha256 of the logical key.
    #:
    #: The logical key is (env, entity_id, bot_id), and ``entity_id`` alone is
    #: 1024 utf8mb4 characters — 4096 bytes, past InnoDB's 3072-byte index-key
    #: cap on its own. Indexing a fixed-width digest instead keeps the wide
    #: columns at their true widths while the constraint stays enforceable.
    #:
    #: Written by the repository, never by a caller. Hex sha256 is 64 ASCII
    #: characters, and the tenant is carried alongside rather than hashed in, so
    #: the isolation boundary stays visible in the key itself.
    script_key = Column(
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
            "script_key",
            name="uk_tenant_script_key",
        ),
    )

    def to_record(self) -> BotStartupScriptRecord:
        """Convert the ORM row to its business record."""
        return BotStartupScriptRecord(
            id=self.id,
            env=self.env,
            entity_id=self.entity_id,
            bot_id=self.bot_id,
            script=self.script,
            size_bytes=self.size_bytes,
            modifier=self.modifier,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


# Confines every read to the request's tenant and stamps it on insert. The
# registrar validates that the mapped column exists — see
# ``utils/avernet_tenant_guard`` for why that check is the thing standing
# between a bad declaration here and a cross-tenant read.
register_avernet_tenant_guard(BotStartupScriptModel)
