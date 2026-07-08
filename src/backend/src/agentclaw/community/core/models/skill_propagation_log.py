"""SkillPropagationLog ORM model — records each propagate_* invocation."""
from sqlalchemy import Column, String, Text, Integer, Index, func

from agentclaw.community.core.base import Base


class SkillPropagationLog(Base):
    """每次 SkillPropagationService 调用的可观测记录。

    propagation_id：UUID，作为 PropagationResult.propagation_log_id 对外暴露。
    status：pending → done / failed。
    """
    __tablename__ = "ac_skill_propagation_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    propagation_id = Column(String(64), unique=True, nullable=False, index=True)
    skill_uuid = Column(String(64), nullable=False, index=True)
    env = Column(String(16), nullable=False)
    action = Column(String(16), nullable=False, comment="upgrade / removal")
    status = Column(String(16), nullable=False, default="pending", comment="pending / done / failed")
    affected_bot_count = Column(Integer, nullable=False, default=0)
    success_bot_count = Column(Integer, nullable=False, default=0)
    failed_bot_ids = Column(Text, nullable=True, comment="JSON array string")
    extra = Column(Text, nullable=True, comment="JSON: new_version etc.")
    error_msg = Column(Text, nullable=True)
    # Production stores these as varchar(64) (DB-clock 'YYYY-MM-DD HH:MM:SS'
    # strings), not DATETIME — see specs/.../ddl-parity-skill_propagation_log.md.
    # DB clock via func.now() (NOW() on OceanBase, CURRENT_TIMESTAMP on SQLite)
    # keeps writes consistent with legacy rows; string compare is portable.
    gmt_created = Column(String(64), server_default=func.now(), nullable=False)
    gmt_modified = Column(
        String(64), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_skill_propagation_log_uuid_env_created", "skill_uuid", "env", "gmt_created"),
        {"extend_existing": True},
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "propagation_id": self.propagation_id,
            "skill_uuid": self.skill_uuid,
            "env": self.env,
            "action": self.action,
            "status": self.status,
            "affected_bot_count": self.affected_bot_count,
            "success_bot_count": self.success_bot_count,
            "failed_bot_ids": self.failed_bot_ids,
            "extra": self.extra,
            "error_msg": self.error_msg,
            "gmt_created": self.gmt_created,
            "gmt_modified": self.gmt_modified,
        }
