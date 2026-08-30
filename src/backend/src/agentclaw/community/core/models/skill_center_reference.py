"""Persistence facts for SC Public Reference batches and items."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import mysql

from agentclaw.community.core.base import Base
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard


UnsignedBigInteger = (
    BigInteger()
    .with_variant(mysql.BIGINT(unsigned=True), "mysql")
    .with_variant(Integer, "sqlite")
)


class SkillCenterReferenceBatchModel(Base):
    __tablename__ = "ac_skill_center_reference_batch"
    __table_args__ = (
        UniqueConstraint(
            "avernet_tenant", "env", "request_id", name="uk_sc_reference_request"
        ),
        UniqueConstraint(
            "avernet_tenant",
            "env",
            "idempotency_key",
            name="uk_sc_reference_idempotency",
        ),
        CheckConstraint("env <> ''", name="ck_sc_reference_batch_env_not_empty"),
    )

    id = Column(UnsignedBigInteger, primary_key=True, autoincrement=True)
    request_id = Column(String(64), nullable=False)
    idempotency_key = Column(String(190), nullable=False)
    request_hash = Column(String(64), nullable=False)
    bot_id = Column(String(100), nullable=False)
    owner_id = Column(String(128), nullable=False)
    skill_set_id = Column(String(64), nullable=False)
    actor_id = Column(String(128), nullable=False)
    env = Column(String(20), nullable=False)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_created = Column(DateTime, server_default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SkillCenterReferenceItemModel(Base):
    __tablename__ = "ac_skill_center_reference_item"
    __table_args__ = (
        UniqueConstraint(
            "avernet_tenant", "env", "reference_id", name="uk_sc_reference_id"
        ),
        UniqueConstraint(
            "avernet_tenant",
            "env",
            "request_id",
            "skill_code",
            name="uk_sc_reference_code",
        ),
        Index(
            "idx_sc_reference_collection",
            "avernet_tenant",
            "env",
            "bot_id",
            "owner_id",
            "skill_set_id",
            "gmt_created",
            "id",
        ),
        Index(
            "idx_sc_reference_request_items",
            "avernet_tenant",
            "env",
            "request_id",
            "id",
        ),
        CheckConstraint(
            "status IN ('QUEUED', 'RESOLVING_VERSION', 'MATERIALIZING', "
            "'ADDING_TO_SKILL_SET', 'PROJECTING_RUNTIME', 'COMPLETED', 'FAILED')",
            name="ck_sc_reference_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_sc_reference_attempt_count"),
        CheckConstraint("env <> ''", name="ck_sc_reference_item_env_not_empty"),
    )

    id = Column(UnsignedBigInteger, primary_key=True, autoincrement=True)
    reference_id = Column(String(64), nullable=False)
    request_id = Column(String(64), nullable=False)
    bot_id = Column(String(100), nullable=False)
    owner_id = Column(String(128), nullable=False)
    skill_set_id = Column(String(64), nullable=False)
    actor_id = Column(String(128), nullable=False)
    skill_code = Column(String(512), nullable=False)
    sc_version_number = Column(String(128), nullable=True)
    skill_version_id = Column(UnsignedBigInteger, nullable=True)
    resolved_skill_id = Column(UnsignedBigInteger, nullable=True)
    status = Column(String(32), nullable=False, server_default="QUEUED")
    attempt_count = Column(Integer, nullable=False, server_default="0")
    error_code = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)
    env = Column(String(20), nullable=False)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_created = Column(DateTime, server_default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


for _model in (SkillCenterReferenceBatchModel, SkillCenterReferenceItemModel):
    register_avernet_tenant_guard(_model)


__all__ = ["SkillCenterReferenceBatchModel", "SkillCenterReferenceItemModel"]
