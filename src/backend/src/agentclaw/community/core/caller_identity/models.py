"""Additive persistence model for service-Bot MCP Caller identity."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import BigInteger, Column, DateTime, Index, String, UniqueConstraint
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.plugin_api.models import AutoIncrementBigInteger
from agentclaw.community.utils.env_utils import get_current_env


class McpCallType(StrEnum):
    """MCP execution identity; old rows default to Owner."""

    OWNER = "owner"
    CALLER = "caller"

    @classmethod
    def parse(cls, value: McpCallType | str | None) -> McpCallType:
        if value is None:
            return cls.OWNER
        if isinstance(value, cls):
            return value
        return cls(value)


class BotMcpCallConfigModel(Base):
    """Sparse Caller overrides; Owner is represented by a missing row."""

    __tablename__ = "ac_bot_mcp_call_config"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    bot_pk = Column(BigInteger, nullable=False, comment="ac_bots.id")
    server_code = Column(String(256), nullable=False)
    engine_type = Column(String(64), nullable=False)
    call_type = Column(String(16), nullable=False)
    modifier_id = Column(String(1024), nullable=False)
    env = Column(String(20), nullable=False, default=get_current_env)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "bot_pk",
            "server_code",
            "engine_type",
            "env",
            name="uk_bot_mcp_call_config_scope",
        ),
        Index(
            "idx_bot_mcp_call_config_aggregate",
            "bot_pk",
            "engine_type",
            "env",
            "call_type",
        ),
    )


__all__ = ["BotMcpCallConfigModel", "McpCallType"]
