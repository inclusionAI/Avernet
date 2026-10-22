"""One current application selection per logical Bot and knowledge base."""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.plugin_api.models import AutoIncrementBigInteger
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard


class BotKnowledgeBindingModel(Base):
    __tablename__ = "ac_bot_knowledge_binding"
    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    env = Column(String(20), nullable=False)
    bot_pk = Column(AutoIncrementBigInteger, nullable=False)
    space_id = Column(AutoIncrementBigInteger, nullable=False)
    kb_id = Column(String(128), nullable=False)
    enabled = Column(Boolean, nullable=False, default=False)
    revision = Column(Integer, nullable=False, default=1)
    updated_by = Column(String(128), nullable=False)
    gmt_modified = Column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )
    __table_args__ = (
        UniqueConstraint(
            "avernet_tenant", "env", "bot_pk", "kb_id", name="uk_bot_knowledge_binding"
        ),
    )


register_avernet_tenant_guard(BotKnowledgeBindingModel)
