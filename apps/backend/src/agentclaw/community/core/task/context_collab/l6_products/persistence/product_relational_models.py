"""Five-table business schema. No runtime snapshot/receipt/outbox/slot models.

Relationships use indexed IDs, not database foreign keys. L6 registers shared
Collet/Event indexes here; L4 has no dependency on L6.
Load this module in the installer before creating any of these five tables.
"""

from sqlalchemy import (
    Column,
    Index,
    Integer,
    String,
    Text,
    TIMESTAMP,
)
from sqlalchemy.dialects import mysql

from agentclaw.community.core.base import Base
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.models import (
    AutoIncrementBigInteger as ID,
    UnsignedAutoIncrementBigInteger as COLLET_ID,
    ContextCollabColletModel as ColletRow,
    ContextCollabEventModel as EventRow,
    _base_axes,
    _medium_text,
)
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard


def exact(size):
    return String(size).with_variant(
        mysql.VARCHAR(size, collation="utf8mb4_bin"), "mysql"
    )


def axes():
    columns = _base_axes()
    columns[0].type = exact(20)
    columns[1].type = exact(64)
    columns[2].type = TIMESTAMP()
    columns[3].type = TIMESTAMP()
    return columns


class ProductProfileRow(Base):
    __tablename__ = "ac_context_collab_product_profile"
    id = Column(ID, primary_key=True, autoincrement=True)
    name = Column(String(256), nullable=False)
    description = Column(String(2048), nullable=False, default="", server_default="")
    config = Column(Text, nullable=False)
    status = Column(exact(32), nullable=False, default="DRAFT", server_default="DRAFT")
    creator = Column(exact(1024), nullable=True)
    modifer = Column(exact(1024), nullable=True)
    env, avernet_tenant, gmt_create, gmt_modified = axes()
    __table_args__ = (
        Index("idx_cc_profile_scope_name", "avernet_tenant", "env", "name"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_bin",
        },
    )


class ColletRolloutRow(Base):
    __tablename__ = "ac_context_collab_collet_rollout"
    id = Column(ID, primary_key=True, autoincrement=True)
    collet_id = Column(COLLET_ID, nullable=False)
    ordinal = Column(Integer, nullable=False)
    predecessor_id = Column(ID, nullable=True)
    content = Column(_medium_text(), nullable=False)
    status = Column(exact(32), nullable=False)
    env, avernet_tenant, gmt_create, gmt_modified = axes()
    __table_args__ = (
        Index("uk_cc_rollout_order", "collet_id", "ordinal", unique=True),
        Index("uk_cc_rollout_collet_id", "collet_id", "id", unique=True),
        Index("idx_cc_rollout_predecessor", "collet_id", "predecessor_id"),
        Index(
            "idx_cc_rollout_scope_collet", "avernet_tenant", "env", "collet_id", "id"
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_bin",
        },
    )


class StageRolloutRow(Base):
    __tablename__ = "ac_context_collab_stage_rollout"
    id = Column(ID, primary_key=True, autoincrement=True)
    collet_rollout_id = Column(ID, nullable=False)
    name = Column(String(256), nullable=False)
    status = Column(exact(32), nullable=False)
    assignee_type = Column(exact(64), nullable=False, default="", server_default="")
    assignee_uuid = Column(exact(256), nullable=False, default="", server_default="")
    input = Column(_medium_text(), nullable=False)
    session_id = Column(exact(512), nullable=False, default="", server_default="")
    acceptances = Column(String(8192), nullable=False)
    error_msg = Column(Text, nullable=False)
    artifacts = Column(_medium_text(), nullable=False)
    env, avernet_tenant, gmt_create, gmt_modified = axes()
    __table_args__ = (
        Index(
            "idx_cc_stage_scope_rollout",
            "avernet_tenant",
            "env",
            "collet_rollout_id",
            "id",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_bin",
        },
    )


# Cross-layer schema assembly is deliberately one-way (L6 -> L4).
for table, name, columns in (
    (
        ColletRow.__table__,
        "idx_cc_collet_profile_status",
        ["avernet_tenant", "env", "product_profile_id", "status", "id"],
    ),
    (ColletRow.__table__, "idx_cc_collet_current", ["id", "current_rollout_id"]),
    (
        EventRow.__table__,
        "idx_cc_event_rollout",
        ["avernet_tenant", "env", "collet_rollout_id", "id"],
    ),
    (
        EventRow.__table__,
        "idx_cc_event_stage",
        ["avernet_tenant", "env", "collet_stage_rollout_id", "id"],
    ),
    (
        EventRow.__table__,
        "idx_cc_event_stage_parent",
        ["collet_rollout_id", "collet_stage_rollout_id"],
    ),
):
    Index(name, *(table.c[column] for column in columns))

for model in (ProductProfileRow, ColletRolloutRow, StageRolloutRow):
    register_avernet_tenant_guard(model)

BUSINESS_TABLES = (
    ProductProfileRow.__table__,
    ColletRow.__table__,
    ColletRolloutRow.__table__,
    StageRolloutRow.__table__,
    EventRow.__table__,
)
