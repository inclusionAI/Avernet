"""
MCP 相关 ORM 模型定义（新架构源码所在地）。

- ``UserMCPConfig`` 由 mcp 模块拥有。
- ``SkillSetMCPServer`` 由 skill_center 模块拥有（表关联定义）。
"""
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import relationship

# 使用 plugins.models.Base 以与 SkillSet 等模型共享同一个 metadata
from agentclaw.community.plugin_api.models import Base
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard


class SkillSetMCPServer(Base):
    """Association table between SkillSet and MCP Server.

    Note: We store server_code and name directly instead of using a foreign key
    to ac_mcp_server table, as MCP server data is managed by MCP Center.
    """
    __tablename__ = "ac_skill_set_mcp"

    id = Column(Integer, primary_key=True, autoincrement=True)
    skill_set_id = Column(Integer, ForeignKey("ac_skill_set.id"), nullable=False, index=True)
    server_code = Column(String(256), nullable=False, index=True)  # MCP server code (e.g., mcp.third.faas...)
    name = Column(String(256), nullable=False)  # MCP server name
    description = Column(Text, nullable=True)  # MCP server description
    icon = Column(String(500), nullable=True)  # MCP server icon URL
    user_id = Column(String(100), nullable=True, index=True)  # 用户工号
    env = Column(String(50), nullable=True)  # 环境标识: dev/pre/prod
    # The association is tenant-owned alongside its Skill Set. Keep the field
    # out of to_dict() so existing Skills API payloads do not change.
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    # DB-owned timestamps: prod ac_user_mcp_config is `timestamp NULL
    # DEFAULT CURRENT_TIMESTAMP [ON UPDATE CURRENT_TIMESTAMP]`. The repo
    # does not set these in Python (matches the prior prod twin's NOW()).
    gmt_created = Column(DateTime, server_default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    skill_set = relationship("SkillSet", back_populates="mcp_servers")

    def to_dict(self) -> dict:
        return {
            "id": str(self.id) if self.id is not None else None,
            "skill_set_id": str(self.skill_set_id) if self.skill_set_id is not None else None,
            "server_code": self.server_code,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "user_id": self.user_id,
            "env": self.env,
            "gmt_created": self.gmt_created.isoformat() if self.gmt_created else None,
            "gmt_modified": self.gmt_modified.isoformat() if self.gmt_modified else None,
        }


# Register where the model is defined so every direct ORM path has the shared
# read/write boundary without per-repository predicates or Session listeners.
register_avernet_tenant_guard(SkillSetMCPServer)


class UserMCPConfig(Base):
    """User-specific MCP Server configuration (API keys, etc.).

    Note: We store server_code directly instead of using a foreign key
    to ac_mcp_server table, as MCP server data is managed by MCP Center.
    """
    __tablename__ = "ac_user_mcp_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), nullable=False, index=True)  # 用户工号
    server_code = Column(String(256), nullable=False, index=True)  # MCP server code
    api_key = Column(String(500), nullable=True)  # LING_XI类型需要的API Key (向后兼容)
    custom_headers = Column(Text, nullable=True)  # JSON: 用户自定义Headers
    extra_config = Column(Text, nullable=True)  # JSON: 其他配置项
    env = Column(String(50), nullable=True)  # 环境标识: dev/pre/prod
    # Data-isolation tenant (see utils/avernet_tenant_guard + the registration
    # below). server_default (not a Python default=) so create_all emits the
    # same DEFAULT 'teamclaw' prod's out-of-band DDL applies, backfilling
    # existing rows and covering any non-ORM insert; the context-aware value on
    # ORM inserts comes from the insert guard. Deliberately absent from
    # to_dict() so no current API response body changes.
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_created = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        # Production's unique key was (user_id, server_code, env) — see
        # specs/2026-05-17-unified-repository-round-2/
        # ddl-parity-ac_user_mcp_config.md. Tenant-isolation prepends
        # avernet_tenant: a user identifier is only meaningful *within* a
        # tenant, so two tenants may each configure their own "12345" for the
        # same MCP server. Without the tenant in the key the second tenant's
        # write fails with a duplicate-key error against a row it cannot see.
        # Adding a leading column only loosens a unique key, so every existing
        # row stays valid. Aligned so the local SQLite test DB enforces the
        # same constraint as prod.
        UniqueConstraint(
            "avernet_tenant",
            "user_id",
            "server_code",
            "env",
            name="uix_user_mcp_config_tenant",
        ),
    )

    def to_dict(self) -> dict:
        import json
        return {
            "id": str(self.id) if self.id is not None else None,
            "user_id": self.user_id,
            "server_code": self.server_code,
            # 以下字段已废弃，仅保留向后兼容，实际使用 extra_config
            "api_key": self.api_key,
            "custom_headers": json.loads(self.custom_headers) if self.custom_headers else {},
            # 统一配置存储在 extra_config 中
            "extra_config": json.loads(self.extra_config) if self.extra_config else {},
            "env": self.env,
            "gmt_created": self.gmt_created.isoformat() if self.gmt_created else None,
            "gmt_modified": self.gmt_modified.isoformat() if self.gmt_modified else None,
        }

    def get_unified_config(self) -> dict:
        """获取统一的配置（从 extra_config 中解析）

        Returns:
            {
                "api_key": str or None,           # API Key（授权格式）
                "headers": dict,                   # 自定义 Headers
                "endpoint_env": str,              # 环境选择：PROD/PRE
            }
        """
        import json
        extra = json.loads(self.extra_config) if self.extra_config else {}
        return {
            "api_key": extra.get("api_key"),
            "headers": extra.get("headers", {}),
            "endpoint_env": extra.get("endpoint_env", "PROD"),  # 默认 PROD
            "transport_protocol": extra.get("transport_protocol"),  # 默认 None
        }


# Confine every read/update/delete to the request's tenant and stamp it on every
# insert. Registered here so the guarantee is welded to the model: import
# UserMCPConfig, get the guard. This table holds third-party API keys and
# authorization headers, so it is the most sensitive data in the mcp category.
register_avernet_tenant_guard(UserMCPConfig)
