"""One current application selection per logical Bot and knowledge base."""

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Boolean,
    Column,
    Index,
    String,
    UniqueConstraint,
    false,
)
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.plugin_api.models import AutoIncrementBigInteger
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard


class BotKnowledgeBindingModel(Base):
    __tablename__ = "ac_bot_knowledge_binding"
    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, comment="主键ID"
    )
    gmt_create = Column(
        TIMESTAMP,
        nullable=False,
        server_default=func.current_timestamp(),
        comment="创建时间",
    )
    # OB deployment DDL also supplies ON UPDATE CURRENT_TIMESTAMP. Keep the
    # ORM-side update expression for portable local SQLite writes.
    gmt_modified = Column(
        TIMESTAMP,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        comment="最后修改时间",
    )
    avernet_tenant = Column(
        String(64),
        nullable=False,
        server_default="teamclaw",
        comment="Avernet数据隔离租户",
    )
    env = Column(String(20), nullable=False, comment="环境标识")
    bot_pk = Column(
        AutoIncrementBigInteger, nullable=False, comment="Bot记录主键，对应ac_bots.id"
    )
    space_id = Column(AutoIncrementBigInteger, nullable=False, comment="绑定所属空间ID")
    kb_id = Column(String(128), nullable=False, comment="知识库ID")
    enabled = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
        comment="是否启用该知识库",
    )
    revision = Column(
        BigInteger,
        nullable=False,
        default=1,
        comment="当前绑定版本号，用于并发修改校验",
    )
    updated_by = Column(String(128), nullable=False, comment="最后修改人标识")
    __table_args__ = (
        UniqueConstraint(
            "avernet_tenant", "env", "bot_pk", "kb_id", name="uk_bot_knowledge_binding"
        ),
        Index(
            "idx_bk_enabled",
            "avernet_tenant",
            "env",
            "bot_pk",
            "space_id",
            "enabled",
            "kb_id",
        ),
        Index("idx_bk_page", "avernet_tenant", "env", "bot_pk", "space_id", "id"),
        {
            "comment": "Bot知识库当前启用配置",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_bin",
        },
    )


register_avernet_tenant_guard(BotKnowledgeBindingModel)
