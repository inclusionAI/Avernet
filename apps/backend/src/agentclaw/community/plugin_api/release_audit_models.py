"""Single-row publication audit persistence schema."""
from __future__ import annotations

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, Text, UniqueConstraint

from agentclaw.community.plugin_api.models import Base
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard

_AutoId = BigInteger().with_variant(Integer, "sqlite")


class ReleaseAuditModel(Base):
    """One audit row per accepted business change."""

    __tablename__ = "ac_service_bot_release_audit"

    id = Column(_AutoId, primary_key=True, autoincrement=True)
    change_id = Column(String(64), nullable=False)
    source_bot_pk = Column(BigInteger, nullable=False)
    bot_id = Column(String(512), nullable=False)
    entity_id = Column(String(1024), nullable=False)
    env = Column(String(64), nullable=False)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    creator_id = Column(String(1024), nullable=True)
    operator_id = Column(String(1024), nullable=False)
    bot_created_at = Column(DateTime, nullable=True)
    change_created_at = Column(DateTime, nullable=False)
    submitted_at = Column(DateTime, nullable=True)
    before_stage = Column(String(16), nullable=True)
    desired_stage = Column(String(16), nullable=False)
    before_status = Column(String(128), nullable=True)
    publish_id = Column(BigInteger, nullable=False)
    version = Column(BigInteger, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    finish_status = Column(String(16), nullable=False)
    final_publish_status = Column(String(128), nullable=True)
    ext = Column(Text, nullable=False, default="{}")

    __table_args__ = (
        UniqueConstraint("avernet_tenant", "env", "change_id", name="uk_release_audit_change"),
        Index("idx_release_audit_bot", "avernet_tenant", "env", "source_bot_pk", "submitted_at"),
        Index("idx_release_audit_publish", "avernet_tenant", "env", "publish_id"),
    )


register_avernet_tenant_guard(ReleaseAuditModel)
