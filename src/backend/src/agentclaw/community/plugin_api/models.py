"""Shared SQLAlchemy declarations for the plugins layer.

Defines the declarative Base used by all ORM models and concrete model
classes that plugin implementations (local / prod) need without pulling
in the full OpenClaw server layer.
"""
import json

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base  # noqa: F401  — canonical registry lives in core/
from agentclaw.community.core.workspace.constants import (  # noqa: E402,F401
    DEFAULT_ENGINE_TYPE,
    SUPPORTED_ENGINE_TYPES,
)
from agentclaw.community.utils.avernet_tenant_guard import (  # noqa: E402
    # Re-exported: CrossTenantInsertError was defined here in Stage 1 and is
    # imported from this module by the bot tenant-guard tests.
    CrossTenantInsertError,  # noqa: F401
    register_avernet_tenant_guard,
)
from agentclaw.community.utils.env_utils import get_current_env  # noqa: E402


# SQLite only auto-increments columns declared as exactly "INTEGER PRIMARY KEY".
# BigInteger renders as "BIGINT" in SQLite, which breaks autoincrement.
# with_variant() makes SQLAlchemy use Integer (→ "INTEGER") on SQLite while
# keeping BigInteger (→ "BIGINT") on MySQL/PostgreSQL.
AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")


class BotModel(Base):
    """SQLAlchemy ORM model for ac_bots table."""
    __tablename__ = "ac_bots"

    # Uses AutoIncrementBigInteger: BigInteger on MySQL, Integer on SQLite.
    # SQLite requires exactly "INTEGER PRIMARY KEY" for autoincrement to work.
    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    bot_id = Column(String(64), nullable=False)
    bot_name = Column(String(1024), nullable=True)
    bot_desc = Column(String(1024), nullable=True)
    entity_id = Column(String(1024), nullable=False)
    entity_type = Column(String(1024), nullable=False)
    creator_id = Column(String(1024), nullable=False)
    owner_id = Column(String(1024), nullable=False)
    owner_name = Column(String(100), nullable=True)
    engine_types = Column(String(2048), default=lambda: json.dumps(SUPPORTED_ENGINE_TYPES), nullable=False)
    active_engine = Column(String(64), default=DEFAULT_ENGINE_TYPE, nullable=False)
    status = Column(String(50), default="PENDING", nullable=False)
    binding_id = Column(BigInteger, nullable=True)
    device_id = Column(String(128), nullable=True)
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    modifier_id = Column(String(1024), nullable=True)
    share_policy = Column(Text, nullable=True)
    is_delete = Column(SmallInteger, default=0, nullable=False)
    public = Column(String(64), default="0", nullable=False)
    ext = Column(Text, nullable=True)
    env = Column(String(20), default=get_current_env, nullable=False)
    bot_type = Column(String(128), default='personal', nullable=True)
    template_type = Column(String(64), nullable=True)  # 模板类型，如 applicationCoding
    call_type = Column(String(16), default="owner", nullable=False)
    caller_config_revision = Column(BigInteger, default=0, nullable=False)
    # Data-isolation tenant (see utils/avernet_tenant_guard + the registration
    # below). server_default (not a Python default=) so create_all emits the
    # same DEFAULT 'teamclaw' prod's out-of-band DDL applies, backfilling
    # existing rows and covering any non-ORM insert; the context-aware value on
    # ORM inserts comes from the before_insert guard. Deliberately absent from
    # to_dict() so no current API response body changes.
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "bot_id": self.bot_id,
            "bot_name": self.bot_name,
            "bot_desc": self.bot_desc,
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "creator_id": self.creator_id,
            "owner_id": self.owner_id,
            "owner_name": self.owner_name,
            "engine_types": json.loads(self.engine_types) if self.engine_types else SUPPORTED_ENGINE_TYPES,
            "active_engine": self.active_engine,
            "status": self.status,
            "binding_id": self.binding_id,
            "device_id": self.device_id,
            "gmt_create": self.gmt_create.isoformat() if self.gmt_create else None,
            "gmt_modified": self.gmt_modified.isoformat() if self.gmt_modified else None,
            "modifier_id": self.modifier_id,
            "share_policy": json.loads(self.share_policy) if self.share_policy else None,
            "is_delete": self.is_delete,
            "public": self.public,
            "ext": json.loads(self.ext) if self.ext else None,
            "env": self.env,
            "bot_type": self.bot_type,
            "template_type": self.template_type,
            "call_type": self.call_type,
            "caller_config_revision": self.caller_config_revision,
        }


# ── Avernet tenant guard ────────────────────────────────────────────
#
# The read guard (a tenant WHERE clause on every SELECT/UPDATE/DELETE) and the
# insert guard (an active stamp on every new row) both live in
# utils/avernet_tenant_guard, which is model-agnostic — Stage 5 guards models
# owned by core/ modules that plugin_api must not import. Registering here keeps
# the guarantee welded to the model: import BotModel, get the guard.
register_avernet_tenant_guard(BotModel)


class ResourceModel(Base):
    __table_args__ = {"extend_existing": True}
    """SQLAlchemy ORM model for ac_resource table.

    This is the canonical definition. Legacy code in services/ re-exports from here.
    """
    __tablename__ = "ac_resource"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    resource_type = Column(String(50), nullable=False)
    status = Column(String(50), default='active', nullable=False)
    gmt_created = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    # Type-specific attributes stored as JSON
    attributes = Column(Text, default='{}')
    # Custom metadata (using 'meta' because 'metadata' is SQLAlchemy reserved)
    meta = Column(Text)
    # Ownership and source
    user_id = Column(String(128))
    created_by = Column(String(128))
    source = Column(String(50))
    bolt_id = Column(String(100), default='default')
    env = Column(String(20), default=get_current_env, nullable=False)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            'id': str(self.id) if self.id is not None else None,
            'name': self.name,
            'resource_type': self.resource_type,
            'status': self.status,
            'gmt_created': self.gmt_created.isoformat() if self.gmt_created else None,
            'gmt_modified': self.gmt_modified.isoformat() if self.gmt_modified else None,
            'attributes': json.loads(self.attributes) if self.attributes else {},
            'metadata': json.loads(self.meta) if self.meta else None,
            'user_id': str(self.user_id) if self.user_id is not None else None,
            'created_by': str(self.created_by) if self.created_by is not None else None,
            'source': self.source,
            'bolt_id': self.bolt_id if self.bolt_id else 'default',
            'env': self.env,
        }


class ChannelConfig(Base):
    """SQLAlchemy ORM model for ac_channel_config table.

    Mirrors the MySQL DDL at services/channel/sql/ac_channel_config.sql.
    """
    __tablename__ = "ac_channel_config"

    # DDL: id bigint(20) unsigned. AutoIncrementBigInteger = BIGINT on
    # MySQL/OceanBase, INTEGER on SQLite (autoincrement parity).
    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    type = Column(String(128), nullable=False)
    description = Column(String(4000), nullable=True)
    identity_id = Column(String(128), nullable=False)
    bind_bot_id = Column(String(256), nullable=False)
    config = Column(String(4000), nullable=False)  # JSON-encoded dict
    status = Column(String(128), nullable=False)
    deleted = Column(Integer, nullable=False, default=0)
    # DB-side timestamps (DDL: DEFAULT CURRENT_TIMESTAMP [ON UPDATE ...]).
    gmt_create = Column(DateTime, server_default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    # DDL: env varchar(20) DEFAULT NULL. The repository always writes
    # get_current_env() on insert and filters by it, so the column is
    # effectively populated though the DDL permits NULL.
    env = Column(String(20), default=get_current_env, nullable=True)
    # stage: 配置阶段，可选值: draft(草稿)/verify(验证中)/online(已上线)，默认 NULL
    stage = Column(String(128), nullable=True)

    __table_args__ = (
        Index(
            "idx_type_id_d_bbi",
            "type",
            "identity_id",
            "deleted",
            "bind_bot_id",
        ),
    )


class OssToNasRecord(Base):
    """SQLAlchemy ORM model for ac_oss_to_nas_record table.

    Tracks OSS → NAS migration status per (staff_no, bot_id). Used by
    the SQLite repository impl; the corp store impl uses raw SQL against the
    same table.
    """
    __tablename__ = "ac_oss_to_nas_record"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    staff_no = Column(String(64), nullable=False)
    bot_id = Column(String(64), nullable=False)
    bot_info = Column(Text, nullable=True)  # JSON: {"storage_dir_name": "..."}
    env = Column(String(16), nullable=False)  # pre / prod
    batch_no = Column(String(64), nullable=False)
    sub_batch_no = Column(String(64), nullable=False)
    storage_status = Column(String(16), nullable=False, default="oss")
    # DB-side timestamps (DDL: DEFAULT CURRENT_TIMESTAMP [ON UPDATE ...]).
    gmt_create = Column(DateTime, server_default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        # Prod DDL declares BOTH unique keys; the SQLite schema must
        # enforce exactly what prod enforces (decision 2026-05-18):
        #  - uk_staff_bot_env (staff_no, bot_id, env)
        #  - uk_staff_bot (staff_no, bot_id) — declared
        #    /*!80000 INVISIBLE */ in prod; an invisible index is hidden
        #    from the optimizer but the UNIQUE constraint is still
        #    enforced, so (staff_no, bot_id) is globally unique
        #    regardless of env on BOTH backends.
        UniqueConstraint("staff_no", "bot_id", "env", name="uk_staff_bot_env"),
        UniqueConstraint("staff_no", "bot_id", name="uk_staff_bot"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "staff_no": self.staff_no,
            "bot_id": self.bot_id,
            "bot_info": json.loads(self.bot_info) if self.bot_info else None,
            "env": self.env,
            "batch_no": self.batch_no,
            "sub_batch_no": self.sub_batch_no,
            "storage_status": self.storage_status,
            "gmt_create": self.gmt_create.isoformat()
            if self.gmt_create
            else None,
            "gmt_modified": self.gmt_modified.isoformat()
            if self.gmt_modified
            else None,
        }


class QualityTaskModel(Base):
    """SQLAlchemy ORM model for ac_bot_quality_task table.

    Quality task management: evaluation, stress testing, etc.
    """
    __tablename__ = "ac_bot_quality_task"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    uuid = Column(String(64), nullable=True, unique=True, index=True)
    task_type = Column(String(32), nullable=True)  # eval, etc.
    biz_type = Column(String(32), nullable=True)  # service_bot_single, etc.
    status = Column(String(32), nullable=True)  # init, env, process, success
    bot_id = Column(String(256), nullable=True)
    owner_id = Column(String(256), nullable=True)
    ext = Column(Text, nullable=True)  # JSON-encoded dict
    operator_id = Column(String(256), nullable=True)
    env = Column(String(32), nullable=True, default=get_current_env)
    gmt_create = Column(DateTime, server_default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "idx_tk_bt_bi_oi_env",
            "task_type",
            "biz_type",
            "bot_id",
            "owner_id",
            "env",
        ),
    )


class CommonConfig(Base):
    """SQLAlchemy ORM model for ac_common_config table."""

    __tablename__ = "ac_common_config"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    gmt_create = Column(DateTime, server_default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    business_code = Column(String(64), nullable=False, default="")
    param_name = Column(String(64), nullable=False, default="")
    param_value = Column(Text, nullable=True)
    business_name = Column(String(64), nullable=True)
    param_code = Column(String(64), nullable=False, default="")
    enable = Column(String(1), nullable=False, default="1")
    ext_info = Column(Text, nullable=True)
    env = Column(String(16), nullable=False, default="")

    __table_args__ = (
        UniqueConstraint(
            "business_code",
            "param_code",
            "env",
            name="uk_business_param_id_env",
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "gmt_create": self.gmt_create.isoformat() if self.gmt_create else None,
            "gmt_modified": self.gmt_modified.isoformat()
            if self.gmt_modified
            else None,
            "business_code": self.business_code,
            "param_name": self.param_name,
            "param_value": self.param_value,
            "business_name": self.business_name,
            "param_code": self.param_code,
            "enable": self.enable,
            "ext_info": self.ext_info,
            "env": self.env,
        }
