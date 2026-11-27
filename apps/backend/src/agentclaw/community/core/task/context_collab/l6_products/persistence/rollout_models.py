"""Product runtime v2 tables. One CAS snapshot is the sole aggregate authority."""

from sqlalchemy import Column, String, Index, ForeignKey, Integer
from sqlalchemy.dialects import mysql
from agentclaw.community.core.base import Base
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.models import (
    AutoIncrementBigInteger,
    _medium_text,
)
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard


def exact(length):
    return String(length).with_variant(
        mysql.VARCHAR(length, collation="utf8mb4_bin"), "mysql"
    )


class Axes:
    env = Column(exact(20), nullable=False)
    avernet_tenant = Column(exact(64), nullable=False)


class ProductConfigurationRow(Axes, Base):
    __tablename__ = "ac_product_configuration_v2"
    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    definition_json = Column(_medium_text(), nullable=False)
    owner_json = Column(String(1024), nullable=False)


class ProductIdentityRow(Axes, Base):
    __tablename__ = "ac_product_identity_v2"
    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    kind = Column(exact(16), nullable=False)
    collet_id = Column(AutoIncrementBigInteger, nullable=False)


class ProductRuntimeRow(Axes, Base):
    __tablename__ = "ac_product_runtime_v2"
    id = Column(AutoIncrementBigInteger, primary_key=True)
    profile_id = Column(
        AutoIncrementBigInteger,
        ForeignKey("ac_product_configuration_v2.id"),
        nullable=False,
    )
    revision = Column(AutoIncrementBigInteger, nullable=False)
    snapshot_json = Column(_medium_text(), nullable=False)


class ProductReceiptRow(Axes, Base):
    __tablename__ = "ac_product_receipt_v2"
    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    actor_key = Column(exact(64), nullable=False)
    request_id = Column(exact(128), nullable=False)
    operation = Column(exact(64), nullable=False)
    request_digest = Column(exact(64), nullable=False)
    result_json = Column(_medium_text(), nullable=False)
    __table_args__ = (
        Index(
            "uk_product_receipt_v2",
            "avernet_tenant",
            "env",
            "actor_key",
            "request_id",
            unique=True,
        ),
    )


class ProductIntentRow(Axes, Base):
    __tablename__ = "ac_product_intent_v2"
    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    collet_id = Column(
        AutoIncrementBigInteger, ForeignKey("ac_product_runtime_v2.id"), nullable=False
    )
    revision = Column(AutoIncrementBigInteger, nullable=False)
    kind = Column(exact(16), nullable=False)
    payload_json = Column(_medium_text(), nullable=False)
    state = Column(exact(16), nullable=False, default="pending")
    logical_key = Column(exact(64), nullable=True)
    claim_owner = Column(exact(64), nullable=False, default="")
    lease_until = Column(AutoIncrementBigInteger, nullable=False, default=0)
    __table_args__ = (
        Index(
            "uk_product_intent_v2_key",
            "avernet_tenant",
            "env",
            "logical_key",
            unique=True,
        ),
        Index(
            "ix_product_intent_v2_ready",
            "avernet_tenant",
            "env",
            "state",
            "lease_until",
        ),
    )


class ProductCandidateSlotRow(Axes, Base):
    """Tenant-wide admission, not an expiring process-local semaphore.

    Unknown dispatch and unconfirmed cancellation MUST retain their slots.
    """

    __tablename__ = "ac_product_candidate_slot_v1"
    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    ordinal = Column(Integer, nullable=False)
    collet_id = Column(AutoIncrementBigInteger, nullable=False)
    rollout_id = Column(AutoIncrementBigInteger, nullable=False)
    __table_args__ = (
        Index(
            "uk_product_candidate_slot_v1",
            "avernet_tenant",
            "env",
            "ordinal",
            unique=True,
        ),
        Index(
            "uk_product_candidate_run_v1",
            "avernet_tenant",
            "env",
            "rollout_id",
            unique=True,
        ),
    )


for model in (
    ProductConfigurationRow,
    ProductIdentityRow,
    ProductRuntimeRow,
    ProductReceiptRow,
    ProductIntentRow,
    ProductCandidateSlotRow,
):
    register_avernet_tenant_guard(model)
