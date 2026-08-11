"""SQLAlchemy ORM models owned by the devices domain.

Relocated out of ``plugins/local/sqlite_models.py``: ``EntityDeviceBinding``
is a shared declarative model mapping the real ``ac_entity_device_binding``
table on every runtime, not a local-only artifact.
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, Index, func

from agentclaw.community.core.base import Base


class EntityDeviceBinding(Base):
    """Device binding record — maps an entity (user/team) to a device.

    This table tracks the lifecycle of device allocations:
        PENDING → ACTIVE → RELEASED

    Each record represents one device binding. An entity can have multiple
    bindings (one per bot/bolt), but typically only one is ACTIVE at a time.

    Fields mirror the relational-store schema exactly, so the single
    repository body returns identical DeviceBindingRecord dataclass instances
    on every deploy profile.
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
