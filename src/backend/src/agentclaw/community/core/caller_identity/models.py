"""Additive persistence model for service-Bot MCP Caller identity."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import BigInteger, Column, DateTime, Index, String, UniqueConstraint
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.plugin_api.models import AutoIncrementBigInteger
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard
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
    # Data-isolation tenant (see utils/avernet_tenant_guard + the registration
    # below). server_default (not a Python default=) so create_all emits the
    # same DEFAULT 'teamclaw' prod's out-of-band DDL applies, backfilling
    # existing rows and covering any non-ORM insert; the context-aware value on
    # ORM inserts comes from the insert guard.
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Unchanged by tenant isolation. ``bot_pk`` is ``ac_bots.id``, a global
    # auto-increment primary key, so a row's tenant is already functionally
    # determined by it and no cross-tenant collision is representable. Contrast
    # ``ac_user_mcp_config``, whose key had to gain the tenant because a user
    # identifier collides across tenants.
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


# Confine every read/update/delete to the request's tenant and stamp it on every
# insert. The rows hang off a bot, which Stage 1 already isolates, so this is a
# second independent barrier — but it is the only one covering the aggregate
# reads (``list_draft_call_types`` and the call-type rollup) that query this
# table by ``bot_pk`` alone and never mention a bot record.
register_avernet_tenant_guard(BotMcpCallConfigModel)


__all__ = ["BotMcpCallConfigModel", "McpCallType"]
