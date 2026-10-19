"""One durable row for each Local Skill upload's security observation."""

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects import mysql

from agentclaw.community.core.base import Base
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard


UnsignedBigInteger = (
    BigInteger()
    .with_variant(mysql.BIGINT(unsigned=True), "mysql")
    .with_variant(Integer, "sqlite")
)
OpaqueId = String(128).with_variant(
    mysql.VARCHAR(128, collation="utf8mb4_bin"), "mysql"
)
ReportText = Text().with_variant(mysql.MEDIUMTEXT(), "mysql")


class LocalSkillSecurityObservationModel(Base):
    """One upload, including its snapshot handoff, scan, and independent sync."""

    __tablename__ = "ac_local_skill_security_observation"
    __table_args__ = (
        UniqueConstraint("observation_id", name="uk_local_security_observation_id"),
        UniqueConstraint(
            "avernet_tenant", "env", "bot_id", "owner_id", "skill_id",
            "upload_order", name="uk_local_security_upload_order",
        ),
        UniqueConstraint(
            "avernet_tenant", "env", "check_id", name="uk_local_security_check"
        ),
        Index(
            "idx_local_security_history",
            "avernet_tenant", "env", "owner_id", "bot_id", "skill_id", "upload_order",
        ),
        Index(
            "idx_local_security_provider",
            "avernet_tenant", "env", "provider_skill_id", "upload_order",
        ),
        Index(
            "idx_local_security_recover_scan",
            "env", "processing_status", "gmt_modified", "avernet_tenant",
        ),
        Index(
            "idx_local_security_recover_sync",
            "env", "sync_status", "gmt_modified", "avernet_tenant",
        ),
        Index(
            "idx_local_security_recover_handoff",
            "env", "handoff_stage", "gmt_created", "avernet_tenant",
        ),
    )

    id = Column(UnsignedBigInteger, primary_key=True, autoincrement=True)
    observation_id = Column(OpaqueId, nullable=False)
    # Per-asset upload sequence allocated while the Bot edit guard is held.
    upload_order = Column(UnsignedBigInteger, nullable=True)
    provider_skill_id = Column(OpaqueId, nullable=False)
    skill_id = Column(UnsignedBigInteger, nullable=True)
    # First recorded upload's UUID, reused on replacement.
    incarnation_id = Column(String(128), nullable=False)
    owner_id = Column(String(128), nullable=False)
    bot_id = Column(String(100), nullable=False)
    creator_id = Column(String(128), nullable=True)
    modifier_id = Column(String(128), nullable=False)
    name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    sha256 = Column(String(71), nullable=False)
    snapshot_ref = Column(String(1028), nullable=False)
    handoff_stage = Column(String(32), nullable=False, server_default="PENDING")
    observation_committed = Column(Integer, nullable=False, server_default="0")
    snapshot_verified = Column(Integer, nullable=False, server_default="0")
    repair_claimed = Column(Integer, nullable=False, server_default="0")
    external_artifact_json = Column(Text, nullable=True)
    check_id = Column(String(128), nullable=True)
    processing_status = Column(String(24), nullable=False, server_default="PENDING")
    risk_status = Column(String(24), nullable=True)
    report_json = Column(ReportText, nullable=True)
    detail_url = Column(String(2048), nullable=True)
    scan_started_at = Column(DateTime, nullable=True)
    scan_completed_at = Column(DateTime, nullable=True)
    error_code = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)
    sync_status = Column(String(24), nullable=False, server_default="NOT_READY")
    sync_error_code = Column(String(128), nullable=True)
    sync_error_message = Column(Text, nullable=True)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    env = Column(String(20), nullable=False)
    gmt_created = Column(DateTime, server_default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


register_avernet_tenant_guard(LocalSkillSecurityObservationModel)

__all__ = ["LocalSkillSecurityObservationModel"]
