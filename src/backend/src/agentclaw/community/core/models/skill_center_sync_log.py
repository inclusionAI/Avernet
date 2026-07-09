"""ac_skill_center_sync_log — skill 同步审计表。

记录每次 force_sync 的结果：pending → success / failed。
"""
from sqlalchemy import Column, Index, Integer, String, Text, func

from agentclaw.community.core.base import Base


class SkillCenterSyncLog(Base):
    """ac_skill_center_sync_log — sync 审计日志。"""
    __tablename__ = "ac_skill_center_sync_log"
    __table_args__ = (
        Index("idx_sync_skill_env_created", "skill_uuid", "env", "gmt_created"),
        {"extend_existing": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    skill_uuid = Column(String(128), nullable=False, index=True)
    version = Column(String(64), nullable=False)
    env = Column(String(20), nullable=False, index=True)
    status = Column(String(20), nullable=False)  # pending / success / failed
    checksum = Column(String(128), nullable=True)
    error_msg = Column(String(500), nullable=True)
    extra = Column(Text, default="{}", nullable=False)
    # Production stores these as varchar(64) DB-clock strings, not
    # DATETIME — see specs/2026-05-17-unified-repository-round-2/
    # ddl-parity-ac_skill_center_sync_log.md (same drift the pilot found
    # on ac_skill_propagation_log). func.now() => 'YYYY-MM-DD HH:MM:SS'
    # on OceanBase / CURRENT_TIMESTAMP on SQLite; lexicographic order =
    # chronological, so find_latest's gmt_created DESC stays correct.
    gmt_created = Column(String(64), server_default=func.now(), nullable=False)
    gmt_modified = Column(
        String(64), server_default=func.now(), onupdate=func.now(), nullable=False,
    )
