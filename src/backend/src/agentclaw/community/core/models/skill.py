"""
Skill 相关 ORM 模型（迁移自 services/openclawserver/server/models/skill.py）。
"""
from agentclaw.community.core.base import Base
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard
from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey, UniqueConstraint, Integer, func
from sqlalchemy.orm import relationship


class SkillSet(Base):
    """SkillSet (Capability Set) model."""
    __tablename__ = "ac_skill_set"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    is_default = Column(Boolean, default=False, nullable=False)
    is_builtin = Column(Boolean, default=False, nullable=False)
    user_id = Column(String(128), nullable=True, index=True)
    gmt_created = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    bolt_id = Column(String(100), nullable=True, default="default", comment="bolt_id")
    env = Column(String(20), default=get_current_env, nullable=False)
    engine_type = Column(String(32), nullable=True, index=True, comment="引擎类型，如 openclaw/moltis/hermes/aicoding/claude_code；is_default 行由应用层保证非空")
    is_active = Column(Boolean, default=False, nullable=False, comment="当前激活的技能集")
    # Persistence-only isolation metadata. The shared guard stamps ORM inserts
    # from the current request tenant; the server default preserves existing
    # internal rows and raw writers during the one-tenant cutover.
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")

    # Relationships
    skills = relationship("SkillSetSkill", back_populates="skill_set", cascade="all, delete-orphan")
    mcp_servers = relationship("SkillSetMCPServer", back_populates="skill_set", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("name", "user_id", "env", "bolt_id", name="uix_skill_set_name_user_env_bot"),
        {"extend_existing": True},
    )

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": str(self.id) if self.id is not None else None,
            "name": self.name,
            "description": self.description,
            "is_default": self.is_default,
            "is_builtin": self.is_builtin,
            "user_id": str(self.user_id) if self.user_id is not None else None,
            "gmt_created": self.gmt_created.isoformat() if self.gmt_created else None,
            "gmt_modified": self.gmt_modified.isoformat() if self.gmt_modified else None,
            "bolt_id": self.bolt_id if self.bolt_id else "default",
            "env": self.env,
            "engine_type": self.engine_type,
            "is_active": self.is_active if self.is_active is not None else False,
        }


register_avernet_tenant_guard(SkillSet)


class Skill(Base):
    """Skill metadata model."""
    __tablename__ = "ac_skill"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    git_path = Column(String(500), nullable=True)
    # NOTE: ``link_name`` is intentionally NOT a stored column —
    # prod ``ac_skill`` DDL has no such column. It is *derived* from
    # ``git_path`` at read time (see ``_skill_to_dict`` in the
    # unified repository). The prior column declaration here was a
    # pre-existing model-vs-DDL divergence that broke every full-row
    # ORM query against prod OceanBase ("Unknown column 'link_name'")
    # once S4 unified the repos to use ORM queries instead of raw
    # SQL with explicit column lists. Fixed in 2026-05-20.
    category = Column(String(50), nullable=True, index=True)
    tags = Column(String(500), nullable=True)
    risk_tags = Column(Text, nullable=True)
    mcp_dependencies = Column(Text, nullable=True)
    input_schema = Column(Text, nullable=True)
    output_schema = Column(Text, nullable=True)
    is_public = Column(Boolean, default=False, nullable=False)
    is_builtin = Column(Boolean, default=False, nullable=False)
    user_id = Column(String(128), nullable=True, index=True)
    gmt_created = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    bolt_id = Column(String(100), nullable=True, default="default", comment="bolt_id")
    env = Column(String(20), default=get_current_env, nullable=False)
    status = Column(String(64), nullable=True, default="DEVELOPING", comment="状态: DEVELOPING/PENDING/PUBLISHED/REJECTED")
    version = Column(Integer, nullable=True, default=1, comment="版本号，整数递增")
    skill_uuid = Column(String(128), nullable=True, index=True, comment="技能身份标识，跨版本不变")
    source_type = Column(String(64), nullable=True, comment="创建方式: upload/git")
    category_path = Column(String(256), nullable=True, comment="类目完整路径(ac_skill_category.code)")
    package_url = Column(String(1028), nullable=True, comment="上传到oss上的可访问的url")
    zip_url = Column(String(1028), nullable=True, comment="用户上传的url")
    # Deliberately absent from to_dict(): tenant is persistence metadata, not
    # part of the established internal Skills response contract.
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")

    __table_args__ = (
        # uix_skill_link_name_env_version dropped along with the
        # link_name column (2026-05-20) — prod DDL has neither.
        UniqueConstraint("skill_uuid", "name", "env", "version", name="uix_skill_uuid_name_env_ver"),
        {"extend_existing": True},
    )

    skill_sets = relationship("SkillSetSkill", back_populates="skill", cascade="all, delete-orphan")

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        import json
        # ``link_name`` is derived from ``git_path`` — prod has no
        # such stored column. Logic mirrors the unified
        # ``_skill_to_dict`` helper (plugins/skill_repository.py).
        git_path = self.git_path
        if git_path and git_path.startswith("git://"):
            link_name = git_path[6:].replace("/", "_")
        else:
            link_name = None
        return {
            "id": str(self.id) if self.id is not None else None,
            "name": self.name,
            "description": self.description,
            "git_path": git_path,
            "link_name": link_name,
            "category": self.category,
            # tags is a raw passthrough — matches the prod twin's
            # _row_to_dict and the unified _skill_to_dict in
            # plugins/skill_repository.py. (risk_tags and
            # mcp_dependencies ARE parsed, per prod's asymmetry —
            # the inconsistency is faithful to prod.)
            "tags": self.tags,
            "risk_tags": json.loads(self.risk_tags) if self.risk_tags else [],
            "mcp_dependencies": json.loads(self.mcp_dependencies) if self.mcp_dependencies else [],
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "is_public": self.is_public,
            "is_builtin": self.is_builtin,
            "user_id": str(self.user_id) if self.user_id is not None else None,
            "gmt_created": self.gmt_created.isoformat() if self.gmt_created else None,
            "gmt_modified": self.gmt_modified.isoformat() if self.gmt_modified else None,
            "bolt_id": self.bolt_id if self.bolt_id else "default",
            "env": self.env,
            "status": self.status or "DEVELOPING",
            "version": self.version if self.version is not None else 1,
            "skill_uuid": self.skill_uuid,
            "source_type": self.source_type,
            "category_path": self.category_path,
            "package_url": self.package_url,
            "zip_url": self.zip_url,
        }


register_avernet_tenant_guard(Skill)


class SkillSetSkill(Base):
    """Association table between SkillSet and Skill."""
    __tablename__ = "ac_skill_set_skill"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    skill_set_id = Column(Integer, ForeignKey("ac_skill_set.id"), nullable=False, index=True)
    skill_id = Column(Integer, ForeignKey("ac_skill.id"), nullable=False, index=True)
    skill_uuid = Column(String(128), nullable=True, index=True, comment="技能唯一标识(跨版本不变)")
    user_id = Column(String(128), nullable=True, index=True)
    gmt_created = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    env = Column(String(20), default=get_current_env, nullable=False)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")

    skill_set = relationship("SkillSet", back_populates="skills")
    skill = relationship("Skill", back_populates="skill_sets")

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": str(self.id) if self.id is not None else None,
            "skill_set_id": str(self.skill_set_id) if self.skill_set_id is not None else None,
            "skill_id": str(self.skill_id) if self.skill_id is not None else None,
            "skill_uuid": self.skill_uuid,
            "user_id": str(self.user_id) if self.user_id is not None else None,
            "gmt_created": self.gmt_created.isoformat() if self.gmt_created else None,
            "gmt_modified": self.gmt_modified.isoformat() if self.gmt_modified else None,
            "env": self.env,
        }


register_avernet_tenant_guard(SkillSetSkill)


class UserDefaultSkillSet(Base):
    """用户默认能力集启用状态关联表"""
    __tablename__ = "ac_user_default_skill_set"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(128), nullable=False, index=True)
    bolt_id = Column(String(100), nullable=False, default="default", comment="bolt_id")
    is_enabled = Column(Integer, default=1, nullable=False, comment="1=启用，0=禁用")
    env = Column(String(20), default=get_current_env, nullable=False)
    engine_type = Column(String(32), nullable=True, index=True, comment="引擎类型")
    gmt_created = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "bolt_id", "engine_type", "env", name="uix_user_default_skill_set_user_bolt_engine_env"),
        {"extend_existing": True},
    )

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": str(self.id) if self.id is not None else None,
            "user_id": str(self.user_id) if self.user_id is not None else None,
            "bolt_id": self.bolt_id if self.bolt_id else "default",
            "is_enabled": self.is_enabled,
            "env": self.env,
            "engine_type": self.engine_type,
            "gmt_created": self.gmt_created.isoformat() if self.gmt_created else None,
            "gmt_modified": self.gmt_modified.isoformat() if self.gmt_modified else None,
        }


class AcSkillMember(Base):
    """Skill 成员关联表

    用于管理技能的成员权限，支持 admin 和 member 两种角色。
    """
    __tablename__ = "ac_skill_member"

    id = Column(Integer, primary_key=True, autoincrement=True)
    skill_uuid = Column(String(128), nullable=False, index=True, comment="技能 UUID")
    user_id = Column(String(256), nullable=False, index=True, comment="成员 ID")
    role = Column(String(256), nullable=False, comment="成员角色")
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        {"extend_existing": True},
    )

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": str(self.id) if self.id is not None else None,
            "skill_uuid": self.skill_uuid,
            "user_id": self.user_id,
            "role": self.role,
            "gmt_create": self.gmt_create.isoformat() if self.gmt_create else None,
            "gmt_modified": self.gmt_modified.isoformat() if self.gmt_modified else None,
        }


class SkillCategory(Base):
    """技能类目表"""
    __tablename__ = "ac_skill_category"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(256), nullable=False, comment="当前类目Code（目录名）")
    name = Column(String(256), nullable=False, comment="类目中文名称（暂用英文占位）")
    parent_code = Column(String(256), nullable=False, default="", index=True, comment="父类目编码，根节点为空字符串")
    path = Column(String(1024), nullable=False, unique=True, comment="物化路径（唯一标识），如 /business/aml/")
    level = Column(Integer, nullable=False, default=0, comment="层级深度，根节点为0")
    sort_order = Column(Integer, nullable=False, default=0, comment="同级排序序号")
    status = Column(Integer, nullable=False, default=1, comment="状态 1启用 0禁用")
    # DB-owned timestamps: prod ac_skill_category is `timestamp NOT NULL
    # DEFAULT CURRENT_TIMESTAMP [ON UPDATE CURRENT_TIMESTAMP]`. func.now()
    # => CURRENT_TIMESTAMP (SQLite) / NOW() (OceanBase); the repo does not
    # set these in Python.
    gmt_created = Column(DateTime, server_default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        {"extend_existing": True},
    )

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": str(self.id) if self.id is not None else None,
            "code": self.code,
            "name": self.name,
            "parent_code": self.parent_code,
            "path": self.path,
            "level": self.level,
            "sort_order": self.sort_order,
            "status": self.status,
            "gmt_created": self.gmt_created.isoformat() if self.gmt_created else None,
            "gmt_modified": self.gmt_modified.isoformat() if self.gmt_modified else None,
        }
