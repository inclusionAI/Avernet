from sqlalchemy import Column, Integer, String, Text, DateTime, SmallInteger, UniqueConstraint, func
from agentclaw.community.plugin_api.models import Base


class DormantNotifyLog(Base):
    __tablename__ = "ac_bot_dormant_notify_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    bot_id = Column(String(64), nullable=False)
    owner_id = Column(String(64), nullable=False)
    entity_id = Column(String(64))
    notify_type = Column(String(32), nullable=False)
    notify_target = Column(String(64))
    notify_source = Column(String(32))
    content = Column(Text)
    dt = Column(String(8), nullable=False)
    send_status = Column(String(16), default="pending")
    error_msg = Column(Text)
    dry_run = Column(SmallInteger, default=0)
    gmt_create = Column(DateTime, default=func.now())
    __table_args__ = (
        UniqueConstraint("bot_id", "owner_id", "dt", "notify_type", name="uk_bot_owner_dt_type"),
    )


class DormantWhitelist(Base):
    __tablename__ = "ac_bot_dormant_whitelist"
    id = Column(Integer, primary_key=True, autoincrement=True)
    bot_id = Column(String(64), nullable=False)
    owner_id = Column(String(64), nullable=False)
    governance_source = Column(String(64))
    reason = Column(String(256))
    created_by = Column(String(64))
    gmt_create = Column(DateTime, default=func.now())
    __table_args__ = (UniqueConstraint("bot_id", "owner_id", name="uk_wl_bot_owner"),)


class DormantExternalInput(Base):
    __tablename__ = "ac_bot_dormant_external_input"
    id = Column(Integer, primary_key=True, autoincrement=True)
    bot_id = Column(String(64), nullable=False)
    owner_id = Column(String(64), nullable=False)
    governance_source = Column(String(64))
    governance_dimension = Column(String(64))
    reason = Column(String(512))
    notify_content = Column(Text)
    dt = Column(String(8), nullable=False)
    processed = Column(SmallInteger, default=0)


class DormantCheckAudit(Base):
    __tablename__ = "ac_bot_dormant_check_audit"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64))
    bot_id = Column(String(64))
    owner_id = Column(String(64))
    check_result = Column(String(32))   # active | inactive | unknown | error
    days_inactive = Column(Integer)
    action_taken = Column(String(32))   # none | warn_enqueued | recycled | skipped
    source = Column(String(32))
    error_msg = Column(Text)            # populated when check_result='error'
    dry_run = Column(SmallInteger, default=0)
    gmt_create = Column(DateTime, default=func.now())
