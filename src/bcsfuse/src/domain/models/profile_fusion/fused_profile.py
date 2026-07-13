"""
FusedProfile - G9 Profile Fusion 数据模型

多参与者 Profile 融合后的超级 BOT Profile。
由 LLM 一次性生成，用作后续对话的 System Prompt。

v2 变更：
- 合并 soul + identity 为 persona（人格画像），避免内容重复
- persona 综合描述身份定位、专业背景、核心能力
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


def _get_current_timestamp() -> str:
    """获取当前时间戳字符串（ISO 格式）"""
    return datetime.now().isoformat()


class ExpertProfile(BaseModel):
    """单个专家的 Profile 信息（用于 G9 总-分-总结构）"""

    worker_id: str = Field(..., description="Worker ID")
    display_name: Optional[str] = Field(None, description="显示名称")
    description: Optional[str] = Field(None, description="专家描述")
    role: Optional[str] = Field(None, description="角色/职位")

    # 核心 Markdown 内容
    soul: Optional[str] = Field(None, description="核心身份定位 (soul_md)")
    agents: Optional[str] = Field(None, description="工作空间配置 (agents_md)")
    tools: Optional[str] = Field(None, description="工具配置 (tools_md)")

    # 扩展内容（包含 identity.md, memory.md 等，可能包含非字符串值如 capabilities 列表）
    contents: dict[str, Any] = Field(default_factory=dict, description="扩展内容 (contents)")

    # 身份和信息（常用字段，便于直接访问）
    identity: Optional[str] = Field(None, description="身份信息 (identity.md)")
    memory: Optional[str] = Field(None, description="经验知识 (memory.md)")

    # 技能
    skills: list[str] = Field(default_factory=list, description="技能列表")

    # 元数据
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展元数据")


class FusedProfile(BaseModel):
    """
    融合后的超级 BOT Profile

    由大模型一次性构建，用作后续对话的 System Prompt（角色设定）。

    输出格式（5 项）：
    - name: 超级 BOT 名称
    - description: 超级 BOT 描述（精简，用于角色概述）
    - persona: 人格画像（身份定位+专业背景+核心能力）
    - memory: 经验知识（融合后）
    - skills: 技能集（合并去重）
    """

    model_config = {"extra": "forbid"}

    # 基本信息
    fused_profile_id: str = Field(..., description="融合 Profile 唯一标识")
    source_participants: list[str] = Field(
        default_factory=list,
        description="来源 participant ID 列表"
    )

    # === LLM 生成的融合内容（用作 System Prompt）===
    name: Optional[str] = Field(
        None,
        description="超级 BOT 名称，如'专家团队：架构师+DBA+开发'"
    )
    description: Optional[str] = Field(
        None,
        description="超级 BOT 描述，精简的角色概述，200字以内"
    )
    persona: Optional[str] = Field(
        None,
        description="人格画像，融合身份定位、专业背景、核心能力（原 soul + identity 合并）"
    )
    memory: Optional[str] = Field(
        None,
        description="经验知识，融合所有 participant 的 memory.md"
    )
    skills: list[str] = Field(
        default_factory=list,
        description="技能名称列表，按名称合并去重"
    )

    # === 原始专家信息（用于总-分-总结构）===
    expert_profiles: list[ExpertProfile] = Field(
        default_factory=list,
        description="原始专家信息列表，用于分维度分析"
    )

    # === 融合元数据 ===
    dedup_count: int = Field(default=0, description="去重跳过的内容数量")
    from_cache: bool = Field(default=False, description="是否来自缓存")
    fusion_strategy: str = Field(default="llm_fusion", description="融合策略")
    created_at: str = Field(default_factory=_get_current_timestamp, description="创建时间")
    participant_count: int = Field(default=0, description="参与融合的 participant 数量")

    # === 发起人信息 ===
    driver_bot_id: Optional[str] = Field(
        None,
        description="发起人 Bot ID，用于标识融合操作者"
    )

    def to_system_prompt(self) -> str:
        """
        转换为 System Prompt 格式

        用于设置大模型的角色设定
        """
        sections = []

        # 核心角色指令：明确告诉 LLM 它是融合后的超级 BOT
        sections.append("# 角色定义\n")
        sections.append("你是一个由多位专家能力融合而成的**超级 BOT**。")
        sections.append("你需要**综合运用所有专家的能力**来回答问题，而不是以单一专家的身份。")
        sections.append("在回答时，你应该：")
        sections.append("1. 整合所有专家的视角和知识")
        sections.append("2. 提供全面、专业的分析与建议")
        sections.append("3. 必要时可以说明不同专家的观点差异")
        sections.append("")

        # 名称和描述
        if self.name:
            sections.append(f"## 团队名称\n{self.name}")
        if self.description:
            sections.append(f"\n{self.description}\n")

        # 人格画像（合并后的 soul + identity）
        if self.persona:
            sections.append("\n## 人格画像\n" + self.persona)

        # 经验知识
        if self.memory:
            sections.append("\n## 经验知识\n" + self.memory)

        # 技能集
        if self.skills:
            sections.append("\n## 技能集")
            for skill in self.skills[:10]:  # 限制数量，保持精简
                sections.append(f"- {skill}")
            if len(self.skills) > 10:
                sections.append(f"- ... 还有 {len(self.skills) - 10} 项技能")

        # 结尾强调
        sections.append("\n---\n")
        sections.append("请以超级 BOT 的身份，综合以上能力来回答用户的问题。")

        return "\n".join(sections)

    # === 向后兼容属性 ===
    @property
    def soul(self) -> Optional[str]:
        """向后兼容：返回 persona"""
        return self.persona

    @property
    def identity(self) -> Optional[str]:
        """向后兼容：返回 persona"""
        return self.persona

    def to_expert_profiles_text(self) -> str:
        """
        生成各专家的详细信息文本（用于总-分-总结构的专家分析部分）

        Returns:
            str: 格式化的专家信息文本
        """
        if not self.expert_profiles:
            return ""

        sections = []
        sections.append("# 专家团队详情\n")
        sections.append("以下是参与本次分析的各位专家的详细背景信息：")
        sections.append("")

        for i, expert in enumerate(self.expert_profiles, 1):
            expert_section = []
            name = expert.display_name or expert.worker_id
            expert_section.append(f"## 专家 {i}: {name}")

            if expert.role:
                expert_section.append(f"**角色**: {expert.role}")

            # 核心身份定位（详细展示）
            if expert.soul:
                soul_text = expert.soul
                # 保留更多原始内容，不作过度截断
                if len(soul_text) > 1500:
                    soul_text = soul_text[:1500] + "..."
                expert_section.append(f"\n**核心身份定位**:\n{soul_text}")

            # 身份信息（详细展示）
            if expert.identity:
                identity_text = expert.identity
                if len(identity_text) > 1000:
                    identity_text = identity_text[:1000] + "..."
                expert_section.append(f"\n**身份背景**:\n{identity_text}")

            # 经验知识（详细展示）
            if expert.memory:
                memory_text = expert.memory
                if len(memory_text) > 1000:
                    memory_text = memory_text[:1000] + "..."
                expert_section.append(f"\n**经验知识**:\n{memory_text}")

            # 技能
            if expert.skills:
                expert_section.append(f"\n**专业技能**: {', '.join(expert.skills[:15])}")
                if len(expert.skills) > 15:
                    expert_section[-1] += f" 等 {len(expert.skills)} 项"

            sections.append("\n".join(expert_section))

        return "\n\n---\n\n".join(sections)

    def has_content(self) -> bool:
        """检查是否有任何内容"""
        return bool(
            self.name or
            self.description or
            self.persona or
            self.memory or
            self.skills
        )

    @classmethod
    def from_worker_profiles(
        cls,
        profiles: list[Any],  # list[WorkerProfileContent]
        fusion_data: dict,
    ) -> "FusedProfile":
        """
        从原始 WorkerProfileContent 列表和融合数据创建 FusedProfile

        Args:
            profiles: 原始的 WorkerProfileContent 列表
            fusion_data: LLM 生成的融合数据（name, description, persona 等）

        Returns:
            FusedProfile 实例
        """
        import uuid

        # 构建 expert_profiles
        expert_profiles = []
        for profile in profiles:
            expert = ExpertProfile(
                worker_id=profile.worker_id,
                display_name=profile.display_name,
                role=profile.display_name,  # 使用 display_name 作为角色
                soul=profile.soul_md,
                identity=profile.contents.get("identity.md"),
                memory=profile.contents.get("memory.md"),
                skills=[s.name for s in profile.skill_sets],
            )
            expert_profiles.append(expert)

        # 解析 skills - 直接使用字符串列表
        skills_data = fusion_data.get("skills", [])
        skills = []
        for s in skills_data:
            if isinstance(s, str):
                skills.append(s)
            elif isinstance(s, dict) and "name" in s:
                skills.append(s["name"])

        # 兼容旧格式：如果返回 soul/identity，合并为 persona
        persona = fusion_data.get("persona")
        if not persona:
            soul = fusion_data.get("soul", "")
            identity = fusion_data.get("identity", "")
            if soul and identity:
                persona = f"{soul}\n\n---\n\n{identity}"
            elif soul:
                persona = soul
            elif identity:
                persona = identity

        return cls(
            fused_profile_id=f"fused-{uuid.uuid4().hex[:8]}",
            source_participants=[p.worker_id for p in profiles],
            name=fusion_data.get("name"),
            description=fusion_data.get("description"),
            persona=persona,
            memory=fusion_data.get("memory"),
            skills=skills,
            expert_profiles=expert_profiles,
            participant_count=len(profiles),
        )


class ProfileFusionResult(BaseModel):
    """Profile 融合结果"""
    model_config = {"extra": "forbid"}

    fusion_id: str = Field(..., description="融合唯一标识")
    fused_profile: FusedProfile = Field(..., description="融合后的 Profile")
    individual_profiles: list[str] = Field(
        default_factory=list,
        description="各 participant 的 worker_id 列表"
    )
    warnings: list[str] = Field(default_factory=list, description="警告信息")
    errors: list[str] = Field(default_factory=list, description="错误信息")
    cache_hit: bool = Field(default=False, description="是否命中缓存")
    fusion_timing_ms: int = Field(default=0, description="融合耗时（毫秒）")


__all__ = [
    "FusedProfile",
    "ExpertProfile",
    "ProfileFusionResult",
]