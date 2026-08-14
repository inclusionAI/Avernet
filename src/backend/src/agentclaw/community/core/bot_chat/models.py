"""ORM models for bot-chat DB queries."""

from sqlalchemy import Column, DateTime, Index, Integer, String, BigInteger, Text, DECIMAL, UniqueConstraint
from sqlalchemy.sql import func

from agentclaw.community.plugin_api.models import (
    AutoIncrementBigInteger,
    Base,
)


class AwLangfuseTrace(Base):
    """SQLAlchemy model for aw_langfuse_traces table."""
    __tablename__ = "aw_langfuse_traces"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    trace_id = Column(String(64))
    gmt_trace = Column(BigInteger)
    name = Column(String(256))
    input = Column(Text)
    output = Column(Text)
    session_id = Column(String(1024))
    user_id = Column(String(128))
    trace_metadata = Column("metadata", Text)
    latency = Column(DECIMAL(10, 3))
    total_cost = Column(DECIMAL(10, 6))
    observations = Column(String(4096))
    bot_id = Column(String(256))
    device_id = Column(String(256))
    real_session_id = Column(String(1024))


class AwLangfuseObservation(Base):
    """SQLAlchemy model for aw_langfuse_observation table."""
    __tablename__ = "aw_langfuse_observation"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    observation_id = Column(String(64))
    trace_id = Column(String(64))
    parent_observation_id = Column(String(64))
    type = Column(String(32))
    name = Column(String(255))
    start_time = Column(BigInteger)
    end_time = Column(BigInteger)
    model = Column(String(255))
    input = Column(Text)
    output = Column(Text)
    metadata_json = Column("metadata", Text)
    level = Column(String(32))
    status_message = Column(Text)
    usage_input_tokens = Column(Integer)
    usage_output_tokens = Column(Integer)
    usage_total_tokens = Column(Integer)
    calculated_total_cost = Column(DECIMAL(20, 10))
    latency = Column(DECIMAL(20, 10))


class AcOtelLogTrace(Base):
    """SQLAlchemy model for AC OTEL trace table."""
    __tablename__ = "ac_otel_log_trace"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    trace_id = Column(String(128), nullable=False)
    biz_task_id = Column(String(256))
    biz_scene = Column(String(256))
    session_id = Column(String(1024))
    session_key = Column(String(1024))
    user_id = Column(String(128))
    bot_id = Column(String(256))
    engine = Column(String(64))
    collector = Column(String(128))
    name = Column(String(256))
    input = Column(Text)
    output = Column(Text)
    metadata_json = Column("metadata", Text)
    start_time_ms = Column(BigInteger)
    end_time_ms = Column(BigInteger)
    latency_ms = Column(DECIMAL(20, 3))
    total_cost = Column(DECIMAL(20, 10))
    usage_input_tokens = Column(Integer)
    usage_output_tokens = Column(Integer)
    usage_total_tokens = Column(Integer)
    payload_digest = Column(String(128))
    gmt_create = Column(DateTime, server_default=func.now())
    gmt_modified = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("trace_id", name="uk_ac_otel_log_trace_id"),
        Index("idx_ac_otel_log_trace_start", "start_time_ms"),
        Index("idx_ac_otel_log_trace_user", "user_id"),
        Index("idx_ac_otel_log_trace_bot", "bot_id"),
        Index("idx_ac_otel_log_trace_session_id", "session_id"),
        Index("idx_ac_otel_log_trace_session_key", "session_key"),
        Index("idx_ac_otel_log_trace_task", "biz_scene", "biz_task_id", "start_time_ms"),
        Index("idx_ac_otel_log_trace_task_only", "biz_task_id", "start_time_ms"),
        Index("idx_ac_otel_log_trace_session", "session_id", "start_time_ms"),
        Index("idx_ac_otel_log_trace_session_key_time", "session_key", "start_time_ms"),
    )


class AcOtelLogObservation(Base):
    """SQLAlchemy model for AC OTEL observation table."""
    __tablename__ = "ac_otel_log_observation"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    observation_id = Column(String(128), nullable=False)
    trace_id = Column(String(128), nullable=False)
    parent_observation_id = Column(String(128))
    biz_task_id = Column(String(256))
    biz_scene = Column(String(256))
    session_id = Column(String(1024))
    session_key = Column(String(1024))
    type = Column(String(64))
    name = Column(String(256))
    model = Column(String(255))
    input = Column(Text)
    output = Column(Text)
    metadata_json = Column("metadata", Text)
    start_time_ms = Column(BigInteger)
    end_time_ms = Column(BigInteger)
    latency_ms = Column(DECIMAL(20, 3))
    status = Column(String(64))
    status_message = Column(Text)
    usage_input_tokens = Column(Integer)
    usage_output_tokens = Column(Integer)
    usage_total_tokens = Column(Integer)
    total_cost = Column(DECIMAL(20, 10))
    payload_digest = Column(String(128))
    gmt_create = Column(DateTime, server_default=func.now())
    gmt_modified = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("observation_id", name="uk_ac_otel_log_observation_id"),
        Index("idx_ac_otel_log_obs_trace", "trace_id"),
        Index("idx_ac_otel_log_obs_parent", "parent_observation_id"),
        Index("idx_ac_otel_log_obs_start", "start_time_ms"),
    )


class AcOtelLogBizRef(Base):
    """Business task to agent-log runtime ID relation."""
    __tablename__ = "ac_otel_log_biz_ref"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    biz_scene = Column(String(256), nullable=False)
    biz_task_id = Column(String(256), nullable=False)
    ref_type = Column(String(128), nullable=False)
    ref_value = Column(String(1024), nullable=False)
    ref_digest = Column(String(128), nullable=False)
    engine = Column(String(64))
    collector = Column(String(128))
    user_id = Column(String(128))
    bot_id = Column(String(256))
    metadata_json = Column("metadata", Text)
    gmt_create = Column(DateTime, server_default=func.now())
    gmt_modified = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            "biz_scene",
            "biz_task_id",
            "ref_type",
            "ref_digest",
            name="uk_ac_otel_log_biz_ref",
        ),
        Index("idx_ac_otel_log_biz_ref_task", "biz_scene", "biz_task_id"),
        Index("idx_ac_otel_log_biz_ref_ref", "ref_type", "ref_digest"),
        Index("idx_ac_otel_log_biz_ref_user", "user_id"),
        Index("idx_ac_otel_log_biz_ref_bot", "bot_id"),
    )


class BcsGroupSession(Base):
    """Read model for resolving a BCS group to its runtime sessions."""

    __tablename__ = "bcs_group_sessions"
    __table_args__ = {"extend_existing": True}

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, server_default=func.now())
    session_id = Column(String(64), nullable=False)
    group_id = Column(String(64), nullable=False)
    env = Column(String(32), nullable=False, default="prod")
    status = Column(String(16), nullable=False, default="running")
    session_kind = Column(String(32), nullable=False, default="chat")
