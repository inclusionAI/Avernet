"""SQLAlchemy ORM models for agentcompute ac_agent_compute_* tables.

Every table carries the mandatory columns: id (autoincrement PK),
gmt_create, gmt_modified, env, tenant, is_deleted. Unique indexes are
scoped (env, tenant, ...). Identifier columns in unique indexes use
utf8mb4_bin collation on MySQL to prevent PAD-SPACE/case-fold merges.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.agentcompute.repository.types import _dumps, _loads, _now_ms

AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")

_ENV_LEN = 20
_TENANT_LEN = 64


def _binary_string(length: int):
    return String(length).with_variant(
        mysql.VARCHAR(length, collation="utf8mb4_bin"), "mysql"
    )


_ID128 = _binary_string(128)
_ID190 = _binary_string(190)


class _AgentComputeBase:
    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    env = Column(String(_ENV_LEN), nullable=False, default=get_current_env)
    tenant = Column(String(_TENANT_LEN), nullable=False)
    is_deleted = Column(Boolean, nullable=False, default=False, server_default="0")
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )


class AcAgentComputeRunModel(_AgentComputeBase, Base):
    __tablename__ = "ac_agent_compute_run"

    run_id = Column(_ID128, nullable=False)
    goal = Column(Text, nullable=False)
    agent_catalog = Column(Text, nullable=True)
    provider = Column(String(64), nullable=True)
    request_metadata = Column(Text, nullable=True)
    status = Column(String(32), nullable=False)
    termination_reason = Column(String(32), nullable=True)
    root_agent_id = Column(_ID128, nullable=True)
    final_answer = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    active_idempotency_key = Column(_ID190, nullable=True)
    started_at = Column(BigInteger, nullable=True)
    ended_at = Column(BigInteger, nullable=True)

    __table_args__ = (
        Index("uk_run_id", "env", "tenant", "run_id", unique=True),
        Index("uk_run_idem", "env", "tenant", "active_idempotency_key", unique=True),
        Index("idx_run_status", "env", "tenant", "status", "gmt_modified"),
    )


class AcAgentComputeAgentModel(_AgentComputeBase, Base):
    __tablename__ = "ac_agent_compute_agent"

    agent_id = Column(_ID128, nullable=False)
    run_id = Column(_ID128, nullable=False)
    parent_agent_id = Column(_ID128, nullable=True)
    depth = Column(Integer, nullable=False, default=0)
    name = Column(String(128), nullable=False)
    role = Column(String(256), nullable=True)
    kind = Column(String(16), nullable=False)
    task_spec = Column(Text, nullable=True)
    spawn_round = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    idempotency_key = Column(_ID190, nullable=False)
    output = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    bot_id = Column(_ID128, nullable=True)
    bot_state = Column(String(32), nullable=True)
    started_at = Column(BigInteger, nullable=True)
    ended_at = Column(BigInteger, nullable=True)

    __table_args__ = (
        Index(
            "uk_agent",
            "env", "tenant", "run_id", "agent_id",
            unique=True,
        ),
        Index(
            "uk_agent_idem",
            "env", "tenant", "run_id", "idempotency_key",
            unique=True,
        ),
        Index("idx_agent_status", "env", "tenant", "run_id", "status"),
        Index("idx_agent_parent", "env", "tenant", "run_id", "parent_agent_id"),
    )


class AcAgentComputeMessageModel(_AgentComputeBase, Base):
    __tablename__ = "ac_agent_compute_message"

    message_id = Column(_ID128, nullable=False)
    run_id = Column(_ID128, nullable=False)
    agent_id = Column(_ID128, nullable=False)
    seq = Column(Integer, nullable=False)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=True)
    decision = Column(Text, nullable=True)
    token_usage = Column(Text, nullable=True)
    artifact_key = Column(String(512), nullable=True)

    __table_args__ = (
        Index(
            "uk_message",
            "env", "tenant", "agent_id", "seq",
            unique=True,
        ),
        Index("idx_message_run_agent", "env", "tenant", "run_id", "agent_id"),
    )


class AcAgentComputeResultShardModel(_AgentComputeBase, Base):
    __tablename__ = "ac_agent_compute_result_shard"

    shard_id = Column(_ID128, nullable=False)
    run_id = Column(_ID128, nullable=False)
    source_agent_id = Column(_ID128, nullable=False)
    payload = Column(Text, nullable=True)
    artifact_key = Column(String(512), nullable=True)
    routed_at = Column(BigInteger, nullable=True)

    __table_args__ = (
        Index("uk_shard", "env", "tenant", "shard_id", unique=True),
        Index("idx_shard_run", "env", "tenant", "run_id"),
        Index(
            "uk_shard_agent",
            "env", "tenant", "run_id", "source_agent_id",
            unique=True,
        ),
    )


class AcAgentComputeRoundModel(_AgentComputeBase, Base):
    __tablename__ = "ac_agent_compute_round"

    round_id = Column(_ID128, nullable=False)
    run_id = Column(_ID128, nullable=False)
    stage_index = Column(Integer, nullable=False)
    orchestrator_steps = Column(Integer, nullable=False, default=0)
    max_subagent_steps = Column(Integer, nullable=False, default=0)
    cohort_agent_ids = Column(Text, nullable=True)
    started_at = Column(BigInteger, nullable=True)
    ended_at = Column(BigInteger, nullable=True)

    __table_args__ = (
        Index(
            "uk_round",
            "env", "tenant", "run_id", "stage_index",
            unique=True,
        ),
    )


class AcAgentComputeGuardEventModel(_AgentComputeBase, Base):
    __tablename__ = "ac_agent_compute_guard_event"

    event_id = Column(_ID128, nullable=False)
    run_id = Column(_ID128, nullable=False)
    agent_id = Column(_ID128, nullable=True)
    kind = Column(String(32), nullable=False)
    details = Column(Text, nullable=True)

    __table_args__ = (
        Index("uk_event", "env", "tenant", "event_id", unique=True),
        Index("idx_event_run_kind", "env", "tenant", "run_id", "kind"),
    )


class AcAgentComputeJobModel(_AgentComputeBase, Base):
    __tablename__ = "ac_agent_compute_job"

    job_id = Column(_ID128, nullable=False)
    run_id = Column(_ID128, nullable=False)
    status = Column(String(20), nullable=False)
    run_at = Column(DateTime, nullable=False)
    claimed_by = Column(String(128), nullable=True)
    claim_epoch = Column(BigInteger, nullable=False, default=0)
    lease_expires_at = Column(DateTime, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    deadline_at = Column(DateTime, nullable=False)
    last_error = Column(Text, nullable=True)
    cancel_requested = Column(Boolean, nullable=False, default=False, server_default="0")

    __table_args__ = (
        Index("uk_job_run", "env", "tenant", "run_id", unique=True),
        Index("idx_job_scope_status_run_at", "env", "tenant", "status", "run_at"),
        Index("idx_job_scope_lease", "env", "tenant", "lease_expires_at"),
    )


ALL_MODELS = (
    AcAgentComputeRunModel,
    AcAgentComputeAgentModel,
    AcAgentComputeMessageModel,
    AcAgentComputeResultShardModel,
    AcAgentComputeRoundModel,
    AcAgentComputeGuardEventModel,
    AcAgentComputeJobModel,
)