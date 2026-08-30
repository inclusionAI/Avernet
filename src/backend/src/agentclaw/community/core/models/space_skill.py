"""Additive ORM facts for the Space Skill domain.

These tables deliberately contain persistence facts only.  Draft command
validation, publication state transitions and runtime delivery belong to later
domain slices; modelling them here would make the migration itself a second
source of domain policy.
"""

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
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel as SpaceMember,
    SpaceModel as Space,
)
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard


UnsignedBigInteger = (
    BigInteger()
    .with_variant(mysql.BIGINT(unsigned=True), "mysql")
    .with_variant(Integer, "sqlite")
)
UnsignedInteger = (
    Integer()
    .with_variant(mysql.INTEGER(unsigned=True), "mysql")
    .with_variant(Integer, "sqlite")
)
TinyInteger = Integer().with_variant(mysql.TINYINT(), "mysql")
MediumText = Text().with_variant(mysql.MEDIUMTEXT(), "mysql")
AutoIncrementBigInteger = UnsignedBigInteger


def _scoped_table_args(*constraints):
    """Add the non-empty environment invariant shared by new domain tables."""
    return (*constraints, CheckConstraint("env <> ''", name="ck_env_not_empty"))


class _ScopedDomainFact:
    """Column mixin for new tenant- and environment-scoped facts."""

    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    env = Column(String(20), nullable=False)
    gmt_created = Column(DateTime, server_default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SkillSpaceBinding(_ScopedDomainFact, Base):
    __tablename__ = "ac_skill_space_binding"
    __table_args__ = _scoped_table_args(
        UniqueConstraint(
            "avernet_tenant", "env", "skill_id", name="uk_skill_ownership"
        ),
        Index("idx_space_skills", "avernet_tenant", "env", "space_id"),
    )

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    skill_id = Column(UnsignedBigInteger, nullable=False)
    space_id = Column(UnsignedBigInteger, nullable=False)
    created_by = Column(String(128), nullable=False)


class SkillGrant(_ScopedDomainFact, Base):
    __tablename__ = "ac_skill_grant"
    __table_args__ = _scoped_table_args(
        UniqueConstraint(
            "avernet_tenant", "env", "skill_id", "user_id", name="uk_skill_grant_user"
        ),
        UniqueConstraint(
            "avernet_tenant",
            "env",
            "skill_id",
            "owner_slot",
            name="uk_skill_active_owner",
        ),
        Index("idx_skill_grant_user", "avernet_tenant", "env", "user_id", "status"),
        CheckConstraint("role IN ('OWNER', 'MANAGER')", name="ck_skill_grant_role"),
        CheckConstraint(
            "status IN ('ACTIVE', 'REVOKED')", name="ck_skill_grant_status"
        ),
        CheckConstraint(
            "(role = 'OWNER' AND status = 'ACTIVE' "
            "AND owner_slot IS NOT NULL AND owner_slot = 1) OR "
            "((role <> 'OWNER' OR status <> 'ACTIVE') AND owner_slot IS NULL)",
            name="ck_skill_active_owner_slot",
        ),
    )

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    skill_id = Column(UnsignedBigInteger, nullable=False)
    user_id = Column(String(128), nullable=False)
    role = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, server_default="ACTIVE")
    owner_slot = Column(TinyInteger, nullable=True)
    granted_by = Column(String(128), nullable=False)
    grant_reason = Column(String(1024), nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    revoked_by = Column(String(128), nullable=True)


class SkillDraftEditLease(_ScopedDomainFact, Base):
    __tablename__ = "ac_skill_draft_edit_lease"
    __table_args__ = _scoped_table_args(
        UniqueConstraint(
            "avernet_tenant", "env", "skill_id", name="uk_skill_edit_lease"
        ),
    )

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    skill_id = Column(UnsignedBigInteger, nullable=False)
    holder_user_id = Column(String(128), nullable=True)
    fencing_token = Column(UnsignedBigInteger, nullable=False, server_default="0")
    acquired_at = Column(DateTime, nullable=True)
    last_takeover_by = Column(String(128), nullable=True)


class SkillVersion(_ScopedDomainFact, Base):
    __tablename__ = "ac_skill_version"
    __table_args__ = _scoped_table_args(
        UniqueConstraint(
            "avernet_tenant",
            "env",
            "skill_id",
            "version_ordinal",
            name="uk_skill_version_ordinal",
        ),
        UniqueConstraint(
            "avernet_tenant",
            "env",
            "skill_id",
            "sc_version_number",
            name="uk_skill_sc_version",
        ),
        UniqueConstraint(
            "avernet_tenant", "env", "publication_attempt_id", name="uk_version_attempt"
        ),
        Index(
            "idx_skill_latest",
            "avernet_tenant",
            "env",
            "skill_id",
            "status",
            "version_ordinal",
        ),
        CheckConstraint(
            "status IN ('MATERIALIZING', 'PUBLISHED')", name="ck_skill_version_status"
        ),
        CheckConstraint("version_ordinal >= 1", name="ck_skill_version_ordinal"),
    )

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    skill_id = Column(UnsignedBigInteger, nullable=False)
    # Space publication Versions reference an Attempt. SC Public lazy
    # materialization has no TeamClaw publication command, so it must remain
    # NULL rather than fabricating an Attempt.
    publication_attempt_id = Column(UnsignedBigInteger, nullable=True)
    version_ordinal = Column(UnsignedInteger, nullable=False)
    status = Column(String(24), nullable=False)
    sc_version_number = Column(String(128), nullable=False)
    sc_skill_id = Column(BigInteger, nullable=True)
    sc_version_id = Column(BigInteger, nullable=True)
    sc_sha256 = Column(String(128), nullable=True)
    name = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    metadata_json = Column(MediumText, nullable=True)
    published_at = Column(DateTime, nullable=True)
    created_by = Column(String(128), nullable=False)


class SkillPublicationAttempt(_ScopedDomainFact, Base):
    __tablename__ = "ac_skill_publication_attempt"
    __table_args__ = _scoped_table_args(
        UniqueConstraint(
            "avernet_tenant", "env", "skill_id", "request_id", name="uk_publish_request"
        ),
        UniqueConstraint("active_skill_key", name="uk_active_skill_publish"),
        Index(
            "idx_publish_skill_history",
            "avernet_tenant",
            "env",
            "skill_id",
            "gmt_created",
        ),
        CheckConstraint(
            "status IN ('PREPARING', 'VALIDATING', 'SCANNING', 'SC_SUBMITTING', "
            "'WAITING_SC', 'RESULT_UNKNOWN', 'MATERIALIZING', 'SUCCEEDED', "
            "'FAILED', 'MANUAL_RECONCILIATION')",
            name="ck_skill_publication_attempt_status",
        ),
        CheckConstraint(
            "target_version_ordinal >= 1", name="ck_attempt_target_ordinal"
        ),
    )

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    skill_id = Column(UnsignedBigInteger, nullable=False)
    request_id = Column(String(128), nullable=False)
    active_skill_key = Column(String(256), nullable=True)
    target_version_ordinal = Column(UnsignedInteger, nullable=False)
    sc_version_number = Column(String(128), nullable=False)
    status = Column(String(32), nullable=False)
    failure_code = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)
    sc_post_started_at = Column(DateTime, nullable=True)
    sc_accepted_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_by = Column(String(128), nullable=False)


for _model in (
    SkillSpaceBinding,
    SkillGrant,
    SkillDraftEditLease,
    SkillVersion,
    SkillPublicationAttempt,
):
    register_avernet_tenant_guard(_model)


__all__ = [
    "SkillDraftEditLease",
    "SkillGrant",
    "SkillPublicationAttempt",
    "SkillSpaceBinding",
    "SkillVersion",
    "Space",
    "SpaceMember",
]
