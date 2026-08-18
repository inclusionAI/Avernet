"""SQLAlchemy models for ``ac_space`` and ``ac_space_member``."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Index, String, UniqueConstraint
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.core.spaces.models import (
    SpaceMemberRecord,
    SpaceRecord,
    SpaceRole,
    SpaceType,
)
from agentclaw.community.plugin_api.models import AutoIncrementBigInteger
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard
from agentclaw.community.utils.env_utils import get_current_env


class SpaceModel(Base):
    __tablename__ = "ac_space"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    space_code = Column(String(64), nullable=False)
    space_type = Column(String(32), nullable=False)
    name = Column(String(128), nullable=False)
    personal_owner_id = Column(String(256), nullable=True)
    sc_team_id = Column(String(64), nullable=True)
    env = Column(String(20), nullable=False, default=get_current_env)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    created_by = Column(String(256), nullable=False)
    updated_by = Column(String(256), nullable=False)
    gmt_created = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "avernet_tenant", "space_code", "env", name="uk_space_code_env"
        ),
        UniqueConstraint(
            "avernet_tenant",
            "personal_owner_id",
            "env",
            name="uk_space_personal_owner_env",
        ),
        UniqueConstraint("sc_team_id", "env", name="uk_sc_team_id_env"),
        Index("idx_space_type_env", "avernet_tenant", "space_type", "env"),
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
    user_id = Column(String(256), nullable=False)
    role = Column(String(32), nullable=False)
    env = Column(String(20), nullable=False, default=get_current_env)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    created_by = Column(String(256), nullable=False)
    gmt_created = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "avernet_tenant",
            "space_id",
            "user_id",
            "env",
            name="uk_space_member_user_env",
        ),
        Index("idx_space_member_user", "avernet_tenant", "user_id", "env"),
        Index(
            "idx_space_member_role",
            "avernet_tenant",
            "space_id",
            "role",
            "env",
        ),
    )

    def to_record(self) -> SpaceMemberRecord:
        return SpaceMemberRecord(
            id=self.id,
            space_id=self.space_id,
            user_id=self.user_id,
            role=SpaceRole(self.role),
            env=self.env,
            created_by=self.created_by,
            gmt_created=self.gmt_created,
            gmt_modified=self.gmt_modified,
        )


register_avernet_tenant_guard(SpaceModel)
register_avernet_tenant_guard(SpaceMemberModel)
