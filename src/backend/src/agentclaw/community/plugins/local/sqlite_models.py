"""
SQLAlchemy ORM models for local (SQLite) mode.

This module is the canonical location for EntityDeviceBinding.
The model is used in local development mode (DATABASE_MODE=sqlite).
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, Index, func

from agentclaw.community.core.base import Base
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard


class EntityDeviceBinding(Base):
    """Device binding record — maps an entity (user/team) to a device.

    This table tracks the lifecycle of device allocations:
        PENDING → ACTIVE → RELEASED

    Each record represents one device binding. An entity can have multiple
    bindings (one per bot/bolt), but typically only one is ACTIVE at a time.

    Fields mirror the ZDAS schema exactly so that ZdasDeviceBindingRepository
    and SqliteDeviceBindingRepository return identical DeviceBindingRecord
    dataclass instances.
    """
    __tablename__ = "ac_entity_device_binding"

    # Primary key — auto-incrementing integer
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Entity identification — who owns this device binding
    # entity_type is one of: "staff", "team", "proj" (see models.EntityType)
    entity_id = Column(String(64), nullable=False)
    entity_type = Column(String(32), nullable=False)

    # Device identification — which device is bound
    # device_id: unique identifier for the device instance
    # device_provider: one of "daas", "arca", "local" (see service.py constants)
    device_id = Column(String(64), nullable=False)
    device_provider = Column(String(32), nullable=False)

    # Environment: "dev", "pre", or "prod"
    env = Column(String(16), nullable=False, default="dev")

    # Device properties — stored as JSON string
    # Contains provider-specific data like sandbox_id, client_id, callback_token, etc.
    device_props = Column(Text, nullable=False, default="{}")

    # Binding status — one of DeviceBindingStatus enum values:
    # "PENDING", "ACTIVE", "FAILED", "RELEASED"
    status = Column(String(16), nullable=False)

    # Allocation metadata
    apply_reason = Column(String(256), nullable=True)
    applied_by = Column(String(64), nullable=False)

    # Release metadata — populated when device is released
    release_reason = Column(String(256), nullable=True)
    released_by = Column(String(64), nullable=True)
    released_at = Column(DateTime, nullable=True)

    # Heartbeat — updated when device reports alive via callback
    last_alive_at = Column(DateTime, nullable=True)

    # Timestamps — auto-managed by SQLAlchemy
    gmt_create = Column(DateTime, nullable=False, default=func.now())
    gmt_modified = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    # Indexes — matching the MySQL schema for query performance
    __table_args__ = (
        Index("idx_entity_status", "entity_type", "entity_id", "status"),
        Index("idx_env_entity_status", "env", "entity_type", "entity_id", "status"),
        Index("idx_device_id", "device_id"),
    )


class DefaultSkillsetMcpExclusion(Base):
    """用户从默认能力集中排除的 MCP 记录。

    此表仅用于 is_default=1 的能力集，禁止用于普通能力集。
    """
    __tablename__ = "ac_default_skillset_mcp_exclusion"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    bot_id = Column(String(64), nullable=False, index=True)
    skill_set_id = Column(Integer, nullable=False, index=True)
    server_code = Column(String(255), nullable=False)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    excluded_at = Column(DateTime, nullable=False, default=func.now())
    gmt_create = Column(DateTime, nullable=False, default=func.now())
    gmt_modified = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("uk_user_bot_skillset_mcp", "avernet_tenant", "user_id", "bot_id", "skill_set_id", "server_code", unique=True),
        {"extend_existing": True},
    )


class DefaultSkillsetSkillExclusion(Base):
    """用户从默认技能集中排除的 Skill 记录。

    此表仅用于 is_default=1 的技能集，禁止用于普通技能集。
    """
    __tablename__ = "ac_default_skillset_skill_exclusion"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    bot_id = Column(String(64), nullable=False, index=True)
    skill_set_id = Column(Integer, nullable=False, index=True)
    skill_id = Column(Integer, nullable=False)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    excluded_at = Column(DateTime, nullable=False, default=func.now())
    gmt_create = Column(DateTime, nullable=False, default=func.now())
    gmt_modified = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("uk_user_bot_skillset_skill", "avernet_tenant", "user_id", "bot_id", "skill_set_id", "skill_id", unique=True),
        {"extend_existing": True},
    )


register_avernet_tenant_guard(DefaultSkillsetMcpExclusion)
register_avernet_tenant_guard(DefaultSkillsetSkillExclusion)
