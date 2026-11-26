"""One append-only row per published ProductProfile version; no runtime state."""

from sqlalchemy import Column, Index, Integer, String
from sqlalchemy.dialects import mysql

from agentclaw.community.core.base import Base
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.models import (
    AutoIncrementBigInteger,
    _base_axes,
    _medium_text,
)
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard


class ProductProfileModel(Base):
    __tablename__ = "ac_context_collab_product_profile"
    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    # Binary collation is essential: profile IDs and tenant/env axes are exact.
    profile_id = Column(
        String(128).with_variant(mysql.VARCHAR(128, collation="utf8mb4_bin"), "mysql"),
        nullable=False,
    )
    profile_version = Column(Integer, nullable=False)
    definition_schema_version = Column(Integer, nullable=False)
    definition_json = Column(_medium_text(), nullable=False)
    definition_digest = Column(
        String(64).with_variant(mysql.CHAR(64), "mysql"), nullable=False
    )
    published_by = Column(_medium_text(), nullable=False)
    env, avernet_tenant, gmt_create, gmt_modified = _base_axes()
    env.type = String(20).with_variant(
        mysql.VARCHAR(20, collation="utf8mb4_bin"), "mysql"
    )
    avernet_tenant.type = String(64).with_variant(
        mysql.VARCHAR(64, collation="utf8mb4_bin"), "mysql"
    )
    __table_args__ = (
        Index(
            "uk_context_collab_product_profile",
            "avernet_tenant",
            "env",
            "profile_id",
            "profile_version",
            unique=True,
        ),
    )


register_avernet_tenant_guard(ProductProfileModel)
