"""
MCP 相关 ORM 模型定义（新架构源码所在地）。

- ``UserMCPConfig`` 由 mcp 模块拥有。
- ``SkillSetMCPServer`` 由 skill_center 模块拥有（表关联定义）。
"""
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import relationship

# 使用 plugins.models.Base 以与 SkillSet 等模型共享同一个 metadata
from agentclaw.community.plugin_api.models import Base


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
    gmt_created = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        # Production's unique key is (user_id, server_code, env) — see
        # specs/2026-05-17-unified-repository-round-2/
        # ddl-parity-ac_user_mcp_config.md. Aligned so the local SQLite
        # test DB enforces the same constraint as prod.
        UniqueConstraint(
            "user_id", "server_code", "env", name="uix_user_mcp_config"
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
