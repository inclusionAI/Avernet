"""
Worker Profile Domain Model

Worker Profile Ingestion Baseline

定义 Worker Profile 归一化模型及相关查询结果模型。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.domain.models.context_fragment import ContextFragment
from src.domain.models.skill_profile import SkillProfile


class ProfileType(str, Enum):
    """
    画像类型枚举

    定义 Worker Profile 的类型。
    """
    DEFAULT = "default"  # 员工默认数字分身
    BOT = "bot"          # 员工创建的 bot


class SourceType(str, Enum):
    """
    来源类型枚举

    定义 Worker Profile 数据的来源类型。
    """
    FILE = "file"              # 文件来源
    API = "api"                # API 来源（未来）
    REGISTRY = "registry"      # 注册中心来源（未来）


class WorkerProfileWarning(BaseModel):
    """
    Worker Profile 警告模型

    表示处理过程中的警告信息。

    Attributes:
        code: 警告代码
        message: 警告消息
        source_path: 相关源路径（可选）
        suggestion: 改进建议（可选）
    """

    code: str = Field(..., min_length=1, description="警告代码")
    message: str = Field(..., min_length=1, description="警告消息")
    source_path: Optional[str] = Field(None, description="相关源路径")
    suggestion: Optional[str] = Field(None, description="改进建议")

    model_config = {
        "extra": "forbid",
    }


class WorkerProfile(BaseModel):
    """
    Worker Profile 归一化模型

    表示从数据源读取并归一化后的 Worker 画像。

    本轮不生成 worker_id，使用 profile_key 作为唯一标识。
    worker_id 的生成是业务层的职责。

    Attributes:
        staff_id: 员工 ID
        profile_id: 画像 ID（default 或 bot id）
        profile_type: 画像类型
        source_type: 来源类型
        source_root: 来源根目录
        context_fragments: 上下文片段列表
        active_skills: 激活技能列表
        searchable_text: 归一化可检索文本
        warnings: 警告列表
    """

    # 标识
    staff_id: str = Field(..., min_length=1, description="员工 ID")
    profile_id: str = Field(..., min_length=1, description="画像 ID (default 或 bot id)")
    profile_type: ProfileType = Field(..., description="画像类型")

    # 来源信息
    source_type: SourceType = Field(
        default=SourceType.FILE,
        description="来源类型"
    )
    source_root: str = Field(..., min_length=1, description="来源根目录")

    # 内容
    context_fragments: list[ContextFragment] = Field(
        default_factory=list,
        description="上下文片段列表"
    )
    active_skills: list[SkillProfile] = Field(
        default_factory=list,
        description="激活技能列表"
    )

    # 可检索文本
    searchable_text: str = Field(default="", description="归一化可检索文本")

    # 精简画像（30字以内）
    short_profile: str = Field(
        default="",
        description="精简画像（30字以内），用于快速展示"
    )

    # 警告
    warnings: list[WorkerProfileWarning] = Field(
        default_factory=list,
        description="警告列表"
    )
    # Phase D2: Metadata for diagnostics (profile format conversion, content stats, etc.)
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata 包含 profile 加载诊断信息、格式转换统计等",
    )

    @property
    def profile_key(self) -> str:
        """
        获取唯一标识键

        格式: {staff_id}:{profile_id}

        Returns:
            唯一标识键
        """
        return f"{self.staff_id}:{self.profile_id}"

    def generate_searchable_text(self) -> str:
        """
        生成可检索文本

        格式（固定顺序）：
        [CONTEXT:{kind}:{content}]... [SKILL:{name}:{description}]...

        顺序：
        1. Context fragments 按 kind 字母序排列
        2. Active skills 按 name 字母序排列

        Returns:
            生成的可检索文本
        """
        parts: list[str] = []

        # Context fragments - 按 kind 字母序
        sorted_fragments = sorted(
            self.context_fragments,
            key=lambda f: f.kind.value
        )
        for fragment in sorted_fragments:
            content_preview = fragment.content[:500] if fragment.content else ""
            parts.append(f"[CONTEXT:{fragment.kind.value}:{content_preview}]")

        # Active skills - 按 name 字母序
        sorted_skills = sorted(self.active_skills, key=lambda s: s.name)
        for skill in sorted_skills:
            desc = skill.description or ""
            parts.append(f"[SKILL:{skill.name}:{desc}]")

        self.searchable_text = " ".join(parts)
        return self.searchable_text

    model_config = {
        "extra": "forbid",
    }


class WorkerProfileScanResult(BaseModel):
    """
    Worker Profile 扫描结果模型

    表示从数据源扫描后的聚合结果。

    Attributes:
        profiles: 扫描到的 WorkerProfile 列表
        scan_warnings: 扫描层面的警告（包括重复冲突等）
        source_roots: 扫描的源根目录列表
    """

    profiles: list[WorkerProfile] = Field(
        default_factory=list,
        description="扫描到的 WorkerProfile 列表"
    )
    scan_warnings: list[WorkerProfileWarning] = Field(
        default_factory=list,
        description="扫描层面的警告"
    )
    source_roots: list[str] = Field(
        default_factory=list,
        description="扫描的源根目录列表"
    )

    @property
    def total_warnings(self) -> int:
        """
        获取总警告数

        包括 scan_warnings 和所有 profile 的 warnings。

        Returns:
            总警告数
        """
        profile_warnings = sum(len(p.warnings) for p in self.profiles)
        return len(self.scan_warnings) + profile_warnings

    model_config = {
        "extra": "forbid",
    }


class ProfileMatchResult(BaseModel):
    """
    Profile 匹配结果模型

    表示单个 Profile 的匹配结果，包含分数和解释。

    Attributes:
        profile: 匹配的 WorkerProfile
        score: 匹配分数（0-1）
        matched_fields: 匹配的字段列表
        reasons: 匹配原因列表
    """

    profile: WorkerProfile = Field(..., description="匹配的 WorkerProfile")
    score: float = Field(..., ge=0, le=1, description="匹配分数")
    matched_fields: list[str] = Field(
        default_factory=list,
        description="匹配的字段列表"
    )
    reasons: list[str] = Field(
        default_factory=list,
        description="匹配原因列表"
    )

    model_config = {
        "extra": "forbid",
    }


class ProfileSearchResult(BaseModel):
    """
    Profile 搜索结果模型

    表示搜索操作的聚合结果。

    Attributes:
        matches: 匹配结果列表
        query: 搜索查询
        total_count: 总匹配数
    """

    matches: list[ProfileMatchResult] = Field(
        default_factory=list,
        description="匹配结果列表"
    )
    query: str = Field(..., description="搜索查询")
    total_count: int = Field(default=0, ge=0, description="总匹配数")

    model_config = {
        "extra": "forbid",
    }


class ProfileRecommendResult(BaseModel):
    """
    Profile 推荐结果模型

    表示推荐操作的聚合结果。

    Attributes:
        recommendations: 推荐结果列表
        context: 推荐 context（问题/任务描述）
        strategy: 推荐策略
    """

    recommendations: list[ProfileMatchResult] = Field(
        default_factory=list,
        description="推荐结果列表"
    )
    context: str = Field(..., description="推荐 context")
    strategy: str = Field(default="baseline", description="推荐策略")

    model_config = {
        "extra": "forbid",
    }


__all__ = [
    # 枚举
    "ProfileType",
    "SourceType",
    # 模型
    "WorkerProfileWarning",
    "WorkerProfile",
    "WorkerProfileScanResult",
    "ProfileMatchResult",
    "ProfileSearchResult",
    "ProfileRecommendResult",
]