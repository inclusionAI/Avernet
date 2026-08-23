"""SQLAlchemy models for ``ac_space`` and ``ac_space_member``."""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.core.spaces.models import (
    SpaceMemberRecord,
    SpaceRecord,
    SpaceRole,
    SpaceType,
)
from agentclaw.community.plugin_api.models import AutoIncrementBigInteger
from agentclaw.community.utils.env_utils import get_current_env


class SpaceModel(Base):
    __tablename__ = "ac_space"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    space_code = Column(String(128), nullable=False)
    space_type = Column(String(16), nullable=False)
    name = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    personal_owner_id = Column(String(128), nullable=True)
    sc_team_id = Column(String(64), nullable=True)
    sc_mapping_status = Column(String(24), nullable=False, server_default="PENDING")
    env = Column(String(20), nullable=False, default=get_current_env)
    created_by = Column(String(128), nullable=False)
    updated_by = Column(String(256), nullable=False)
    deleted_at = Column(DateTime, nullable=True)
    deleted_by = Column(String(128), nullable=True)
    gmt_created = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("env", "space_code", name="uk_space_code"),
        UniqueConstraint(
            "env",
            "personal_owner_id",
            name="uk_personal_space",
        ),
        UniqueConstraint("sc_team_id", "env", name="uk_sc_team_id_env"),
        Index("idx_space_sc_team", "env", "sc_team_id"),
        CheckConstraint("space_type IN ('PERSONAL', 'TEAM')", name="ck_space_type"),
        CheckConstraint(
            "sc_mapping_status IN ('PENDING', 'ACTIVE', 'INACTIVE', 'CLEANUP_FAILED')",
            name="ck_space_mapping_status",
        ),
        CheckConstraint("env <> ''", name="ck_space_env_not_empty"),
    )

    def to_record(self) -> SpaceRecord:
        return SpaceRecord(
            id=self.id,
            space_code=self.space_code,
            space_type=SpaceType(self.space_type),
            name=self.name,
            personal_owner_id=self.personal_owner_id,
            sc_team_id=self.sc_team_id,
            env=self.env,
            created_by=self.created_by,
            updated_by=self.updated_by,
            gmt_created=self.gmt_created,
            gmt_modified=self.gmt_modified,
        )


class SpaceMemberModel(Base):
    __tablename__ = "ac_space_member"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    space_id = Column(AutoIncrementBigInteger, nullable=False)
    user_id = Column(String(128), nullable=False)
    user_name = Column(String(128), nullable=True)
    role = Column(String(24), nullable=False)
    status = Column(String(16), nullable=False, server_default="ACTIVE")
    env = Column(String(20), nullable=False, default=get_current_env)
    created_by = Column(String(128), nullable=False)
    removed_at = Column(DateTime, nullable=True)
    removed_by = Column(String(128), nullable=True)
    gmt_created = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("env", "space_id", "user_id", name="uk_space_member"),
        Index("idx_space_member_user", "env", "user_id", "status"),
        CheckConstraint(
            "role IN ('ADMIN', 'ADMINISTRATOR', 'MEMBER')", name="ck_space_member_role"
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')", name="ck_space_member_status"
        ),
        CheckConstraint("env <> ''", name="ck_space_member_env_not_empty"),
    )

    def to_record(self) -> SpaceMemberRecord:
        # Historical ADMINISTRATOR rows are exposed as the canonical ADMIN role.
        return SpaceMemberRecord(
            id=self.id,
            space_id=self.space_id,
            user_id=self.user_id,
            user_name=self.user_name,
            role=(
                SpaceRole.ADMIN
                if self.role in ("ADMIN", "ADMINISTRATOR")
                else SpaceRole.MEMBER
            ),
            env=self.env,
            created_by=self.created_by,
            gmt_created=self.gmt_created,
            gmt_modified=self.gmt_modified,
        )
