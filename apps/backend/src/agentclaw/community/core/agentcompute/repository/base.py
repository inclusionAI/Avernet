"""Shared ORM plumbing for the ac_agent_compute_* table models.

Every table model imports the mandatory-column mix-in and the identifier
column helpers from here; per-table models live in repository/<table>/model.py.
"""
from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String
from sqlalchemy.dialects import mysql
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.utils.env_utils import get_current_env

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
