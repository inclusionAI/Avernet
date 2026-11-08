"""The independent expert directory; invalid scans never replace valid input."""

from sqlalchemy import Boolean, Column, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.dialects.mysql import LONGTEXT

from agentclaw.community.core.base import Base
from agentclaw.community.plugin_api.models import AutoIncrementBigInteger


class ExpertModel(Base):
    __tablename__ = "ac_experts"
    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    avernet_tenant = Column(String(64), nullable=False)
    env = Column(String(20), nullable=False)
    source_repo_id = Column(String(64), nullable=False)
    expert_id = Column(String(64), nullable=False)
    name = Column(String(40), nullable=False)
    description = Column(String(200), nullable=False)
    category = Column(String(40), nullable=False)
    tags = Column(Text, nullable=False)
    enabled = Column(Boolean, nullable=False)
    is_deleted = Column(Boolean, nullable=False, default=False)
    first_added_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    source_commit = Column(String(40), nullable=False)
    expert_path = Column(String(256), nullable=False)
    content_digest = Column(String(64), nullable=False)
    effective_input = Column(
        Text().with_variant(LONGTEXT(), "mysql", "mariadb"), nullable=False
    )
    last_seen_commit = Column(String(40), nullable=False)
    sync_error = Column(Text, nullable=True)
    __table_args__ = (
        UniqueConstraint(
            "avernet_tenant",
            "env",
            "source_repo_id",
            "expert_id",
            name="uk_expert_scope",
        ),
        Index(
            "idx_expert_discovery",
            "avernet_tenant",
            "env",
            "source_repo_id",
            "enabled",
            "is_deleted",
        ),
    )
