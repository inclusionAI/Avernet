"""
Worker Profile Content Domain Model

Profile API MVP - API 注册的 Profile 内容模型

表达通过 API 注册进来的 Worker Profile 原始内容，
不依赖文件系统，支持直接通过 API 管理。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Optional

from pydantic import BaseModel, Field


class ProfileContentType(str, Enum):
    """Profile 内容类型"""
    API = "api"      # API 注册
    FILE = "file"    # 文件导入（兼容）


class SkillSet(BaseModel):
    """
    技能集模型

    表示 Profile 关联的技能信息。
    """
    name: str = Field(..., min_length=1, description="技能名称")
    description: Optional[str] = Field(None, description="技能描述")
    content: Optional[str] = Field(None, description="技能详细内容")
    metadata: dict[str, Any] = Field(default_factory=dict, description="技能元数据")

    model_config = {"extra": "forbid"}


class WorkerProfileContent(BaseModel):
    """
    Worker Profile Content 模型

    表示通过 API 注册的 Worker Profile 完整内容。

    设计理念：
    1. 核心 md 文件（高频访问）使用独立字段，便于索引和查询
    2. 其他 md 文件存储在 contents JSON Map 中，灵活扩展
    3. 通过 get_content(name) 统一访问，屏蔽存储差异

    Attributes:
        worker_id: 关联的 Worker ID
        profile_id: Profile 标识（支持多 profile）
        display_name: 显示名称
        soul_md: SOUL.md 内容（核心身份）- 高频访问
        agents_md: AGENTS.md 内容（工作空间配置）- 高频访问
        tools_md: TOOLS.md 内容（工具配置）- 高频访问
        boot_md: BOOT.md 内容（启动配置）
        heartbeat_md: HEARTBEAT.md 内容（心跳配置）
        contents: 扩展内容 JSON Map - 支持任意 md 文件扩展
        skill_sets: 技能集列表
        metadata: 扩展元数据
        content_type: 内容来源类型
        is_active: 是否为活跃 profile
        created_at: 创建时间
        updated_at: 更新时间
        version: 版本号
    """
    # 标识
    worker_id: str = Field(..., min_length=1, description="关联的 Worker ID")
    profile_id: str = Field(default="default", min_length=1, description="Profile 标识")

    # 显示名称
    display_name: Optional[str] = Field(None, description="显示名称")

    # 描述（从 Worker identity.description 同步）
    description: Optional[str] = Field(None, description="描述 - 来自 Worker identity.description")

    # 核心 Markdown 内容（高频访问，独立字段便于索引）
    soul_md: Optional[str] = Field(None, description="SOUL.md 内容 - 核心身份定义")
    agents_md: Optional[str] = Field(None, description="AGENTS.md 内容 - 工作空间配置")
    tools_md: Optional[str] = Field(None, description="TOOLS.md 内容 - 工具配置")
    boot_md: Optional[str] = Field(None, description="BOOT.md 内容 - 启动配置")
    heartbeat_md: Optional[str] = Field(None, description="HEARTBEAT.md 内容 - 心跳配置")

    # 扩展内容 JSON Map（灵活支持任意内容）
    # 格式: {"profile": "内容", "capabilities": ["..."], ...}
    contents: dict[str, Any] = Field(
        default_factory=dict,
        description="扩展内容 JSON Map，支持任意类型，向量化时自动转为字符串"
    )

    # 技能集
    skill_sets: list[SkillSet] = Field(default_factory=list, description="技能集列表")

    # 扩展元数据
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展元数据")

    # LLM 生成的能力分析存储在 contents 中：
    # - contents["profile"]: 大模型生成的语义能力画像
    # - contents["capabilities"]: 大模型生成的能力标签列表
    # 通过 get_llm_profile() 和 get_llm_capabilities() 方法访问

    # 来源与状态
    content_type: ProfileContentType = Field(
        default=ProfileContentType.API,
        description="内容来源类型"
    )
    is_active: bool = Field(default=False, description="是否为活跃 profile")

    # 时间戳
    created_at: Optional[datetime] = Field(None, description="创建时间")
    updated_at: Optional[datetime] = Field(None, description="更新时间")

    # 版本
    version: int = Field(default=1, ge=1, description="版本号")

    # 核心内容名称常量（用于 get_content）
    CORE_CONTENT_NAMES: ClassVar[frozenset[str]] = frozenset([
        "soul.md", "agents.md", "tools.md", "boot.md", "heartbeat.md"
    ])

    @property
    def profile_key(self) -> str:
        """
        获取 Profile Key

        格式: {worker_id}:{profile_id}

        Returns:
            Profile Key 字符串
        """
        return f"{self.worker_id}:{self.profile_id}"

    def get_content(self, name: str) -> Optional[str]:
        """
        获取指定名称的内容（统一访问接口）

        优先从核心字段获取，否则从 contents Map 获取。

        Args:
            name: 内容名称，如 "soul.md", "custom.md", "docs/api.md"

        Returns:
            内容字符串，不存在返回 None
        """
        # 标准化名称（统一小写）
        normalized_name = name.lower()

        # 核心字段映射
        core_mapping = {
            "soul.md": self.soul_md,
            "agents.md": self.agents_md,
            "tools.md": self.tools_md,
            "boot.md": self.boot_md,
            "heartbeat.md": self.heartbeat_md,
        }

        # 优先从核心字段获取
        if normalized_name in core_mapping:
            return core_mapping[normalized_name]

        # 从扩展内容 Map 获取
        return self.contents.get(normalized_name)

    def set_content(self, name: str, content: Optional[str]) -> None:
        """
        设置指定名称的内容（统一写入接口）

        核心字段使用独立属性，其他内容写入 contents Map。

        Args:
            name: 内容名称
            content: 内容字符串，None 表示删除
        """
        normalized_name = name.lower()

        # 核心字段处理
        if normalized_name == "soul.md":
            self.soul_md = content
        elif normalized_name == "agents.md":
            self.agents_md = content
        elif normalized_name == "tools.md":
            self.tools_md = content
        elif normalized_name == "boot.md":
            self.boot_md = content
        elif normalized_name == "heartbeat.md":
            self.heartbeat_md = content
        else:
            # 扩展内容存储到 Map
            if content is None:
                self.contents.pop(normalized_name, None)
            else:
                self.contents[normalized_name] = content

    def list_content_names(self) -> list[str]:
        """
        列出所有内容名称

        Returns:
            内容名称列表
        """
        names = []

        # 添加有内容的核心字段
        if self.soul_md:
            names.append("soul.md")
        if self.agents_md:
            names.append("agents.md")
        if self.tools_md:
            names.append("tools.md")
        if self.boot_md:
            names.append("boot.md")
        if self.heartbeat_md:
            names.append("heartbeat.md")

        # 添加扩展内容
        names.extend(self.contents.keys())

        return sorted(names)

    # ========================================================================
    # LLM 分析结果便捷访问（存储在 contents 中）
    # ========================================================================

    def get_llm_profile(self) -> Optional[str]:
        """
        获取 LLM 生成的语义能力画像

        Returns:
            语义能力画像文本或 None
        """
        return self.contents.get("profile")

    def set_llm_profile(self, profile: Optional[str]) -> None:
        """
        设置 LLM 生成的语义能力画像

        Args:
            profile: 语义能力画像文本，None 表示删除
        """
        if profile is None:
            self.contents.pop("profile", None)
        else:
            self.contents["profile"] = profile

    def get_llm_capabilities(self) -> list[str]:
        """
        获取 LLM 生成的能力标签列表

        Returns:
            能力标签列表
        """
        capabilities = self.contents.get("capabilities")
        if isinstance(capabilities, list):
            return [str(c) for c in capabilities]
        return []

    def set_llm_capabilities(self, capabilities: list[str]) -> None:
        """
        设置 LLM 生成的能力标签列表

        Args:
            capabilities: 能力标签列表
        """
        if capabilities:
            self.contents["capabilities"] = capabilities
        else:
            self.contents.pop("capabilities", None)

    def generate_searchable_text(self) -> str:
        """
        生成可检索文本

        将所有 markdown 内容和技能信息合并为可检索文本。

        Returns:
            可检索文本
        """
        parts: list[str] = []

        # 添加显示名称
        if self.display_name:
            parts.append(f"[NAME:{self.display_name}]")

        # 添加描述
        if self.description:
            parts.append(f"[DESC:{self.description}]")

        # 添加核心 markdown 内容预览
        if self.soul_md:
            parts.append(f"[SOUL:{self.soul_md[:500]}]")
        if self.agents_md:
            parts.append(f"[AGENTS:{self.agents_md[:500]}]")
        if self.tools_md:
            parts.append(f"[TOOLS:{self.tools_md[:300]}]")

        # 添加扩展内容预览（取前 3 个，支持任意类型）
        for name, content in list(self.contents.items())[:3]:
            if not content:
                continue
            # 将任意类型转为字符串
            if isinstance(content, list):
                content_str = ", ".join(str(item) for item in content[:5])  # 最多取5个元素
            elif isinstance(content, dict):
                content_str = ", ".join(f"{k}={v}" for k, v in list(content.items())[:3])
            else:
                content_str = str(content)
            if content_str:
                parts.append(f"[{name.upper()}:{content_str[:200]}]")

        # 添加技能信息
        for skill in self.skill_sets:
            desc = skill.description or ""
            parts.append(f"[SKILL:{skill.name}:{desc}]")

        # 添加元数据中的关键信息
        if "expertise" in self.metadata:
            parts.append(f"[EXPERTISE:{self.metadata['expertise']}]")
        if "domains" in self.metadata:
            domains = self.metadata['domains']
            if isinstance(domains, list):
                parts.append(f"[DOMAINS:{','.join(domains)}]")

        return " ".join(parts)

    model_config = {"extra": "forbid"}


class WorkerProfileContentList(BaseModel):
    """Profile 列表响应"""
    items: list[WorkerProfileContent] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)
    active_profile_id: Optional[str] = Field(None, description="当前活跃的 profile_id")


__all__ = [
    "ProfileContentType",
    "SkillSet",
    "WorkerProfileContent",
    "WorkerProfileContentList",
]