"""
ProfileAnalyzerService

基于大模型的 Bot Profile 能力分析服务。

在创建/更新 Profile 时，调用 LLM 分析 Profile 内容：
1. 生成语义能力画像（基于证据的角色定位、核心职责、典型任务、能力边界）
2. 从预定义标签体系中选择匹配的 3 个能力标签（附置信度和证据理由）
3. （可选）通过 Bot 认知服务获取 Bot 摘要，增强经验能力分析

设计原则：
- LLM 调用失败不阻塞 profile 创建
- 标签选择限定在预定义的 77 个标签内
- 采用"证据匹配式"筛选，而非主观联想
- 遵循项目现有 LLM 集成模式（LLMGatewayService）
- Bot 认知获取失败时优雅降级，不阻塞主流程
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from src.domain.models.llm_request import LLMRequest
from src.domain.models.llm_task_spec import (
    Complexity,
    CostSensitivity,
    LLMTaskSpec,
    TaskType,
)
from src.domain.models.worker_profile_content import WorkerProfileContent
from src.application.utils.drm_config_helper import profile_prompt_template

if TYPE_CHECKING:
    from src.domain.protocols.bot_cognition_protocol import BotCognitionProvider

logger = logging.getLogger(__name__)

_MAX_TAGS = 3


SYSTEM_PROMPT = "你是一个专业的企业 AI Bot 能力标签评估器。"

if profile_prompt_template is not None and profile_prompt_template != "":
    USER_PROMPT_TEMPLATE = profile_prompt_template
else:
    USER_PROMPT_TEMPLATE = """
请阅读以下agent配置文件，生成一个精简的profile用于agent职责与能力的匹配。

## 基本信息
名称: {display_name}
描述: {description}

{soul_md_section}

{identity_md_section}

{memory_md_section}

{bot_cognition_section}

{skills_list_section}

请按以下格式输出：

## 一句话简介
[用不超过30字的一句话概括该Agent的核心定位和能力，用于快速匹配和展示]

## 职责定位
名称: [原始bot名称]
描述: [原始bot描述]
身份: [用自然语言一句话描述agent身份]
目标: [用自然语言一句话描述核心目标]

## 经验能力
- [领域A]:[用自然语言一句话描述领域A核心能力]
- [领域B]:[用自然语言一句话描述领域B核心能力]
...

## SKILL能力
- [技能A]: [用自然语言一句话描述技能A用途和触发场景]
- [技能B]: [用自然语言一句话描述技能B用途和触发场景]
...

## 能力标签

标签列表: [从候选项中选择3个最匹配的标签]

---

【能力标签候选项】（共71个）

研发能力: 代码开发、代码审查、架构设计、性能优化、问题排查、技术调研
算法能力: 机器学习、深度学习、风控模型、推荐算法、搜索算法、NLP处理、特征工程、模型评估
数据能力: 数据分析、SQL开发、数仓建设、数据开发、数据可视化、实时计算、数据治理、归因分析
运维能力: 容器运维、监控告警、故障排查、发布部署、容量规划、数据库运维、安全运维
测试能力: 测试设计、自动化测试、性能测试、安全测试
风控与安全: 风险识别、规则引擎、反欺诈、信用评估、案件分析、黑产识别、反洗钱、资金安全、商户风控、营销风控、账号安全、交易风控、安全审计、内容安全、应用安全、数据安全
合规能力: 合规审查、反洗钱合规、隐私合规
产品运营: 需求分析、用户研究、产品设计、运营分析、活动策划、用户运营
AI大模型: 模型训练、模型微调、Prompt工程、Agent编排、RAG系统、知识库构建、多模态处理、模型服务化
项目协作: 项目管理、需求评审
客户服务: 服务质量
财务能力: 财务分析、结算核算

---

注意：
- 职责定位从基本信息、IDENTITY.md、SOUL.md提取
- 经验能力从MEMORY.md提取
- SKILL能力从skill_list提取，跳过"默认技能集"
- 能力标签从候选项中选择3个以内**强相关**的标签,标签用逗号分隔
- 未配置的字段输出固定为'无',不需要有任务解释
- 保持精简，便于向量索引检索
- 请用一句话自然语言描述，不要纯关键词堆砌，提高向量召回成功率
- 不要包含负面描述，避免误召回
"""

@dataclass(frozen=True)
class ProfileAnalysisResult:
    """Profile 分析结果"""

    semantic_profile: Optional[str]
    capability_tags: list[str]
    llm_success: bool
    short_profile: Optional[str] = None  # 新增：精简版画像（30字以内）
    error_message: Optional[str] = None


class ProfileAnalyzerService:
    """
    基于 LLM 的 Bot Profile 能力分析服务

    Args:
        llm_gateway: LLM Gateway Service 实例
        cognition_provider: Bot 认知 Provider 实例（可选，用于获取 Bot 认知摘要）
    """

    def __init__(
        self,
        llm_gateway: "LLMGatewayService",
        cognition_provider: Optional["BotCognitionProvider"] = None,
    ) -> None:
        self._gateway = llm_gateway
        self._cognition_provider = cognition_provider

    def analyze(self, content: WorkerProfileContent) -> ProfileAnalysisResult:
        """
        分析 Profile 内容，生成语义画像和能力标签

        Args:
            content: Worker Profile 内容

        Returns:
            ProfileAnalysisResult: 分析结果
        """
        # TODO: 需打通worker_id与bot_id
        bot_id = content.worker_id
        bot_cognition = self._fetch_bot_cognition(bot_id)

        # 调用 LLM
        try:
            result = self._call_llm_analyze(content, bot_cognition)
            return result
        except Exception as e:
            logger.warning(f"[ProfileAnalyzer] LLM 分析失败: {e}")
            return ProfileAnalysisResult(
                semantic_profile=None,
                capability_tags=[],
                llm_success=False,
                error_message=str(e),
            )

    def _fetch_bot_cognition(self, bot_id: str) -> Optional[str]:
        """
        获取 Bot 认知摘要

        Args:
            bot_id: Bot ID

        Returns:
            Bot 的认知摘要，不存在或获取失败返回 None
        """
        if not self._cognition_provider:
            return None

        try:
            cognition = self._cognition_provider.get_bot_cognition(bot_id)
            if not cognition or not cognition.summary:
                logger.debug(f"[ProfileAnalyzer] Bot 认知摘要不可用: bot_id={bot_id}")
                return None

            logger.info(f"[ProfileAnalyzer] Bot 认知摘要获取成功: bot_id={bot_id}")
            return cognition.summary

        except Exception as e:
            logger.warning(f"[ProfileAnalyzer] Bot 认知获取失败: bot_id={bot_id}, error={e}")
            return None

    def _call_llm_analyze(
        self,
        content_obj: WorkerProfileContent,
        bot_cognition: Optional[str] = None,
    ) -> ProfileAnalysisResult:
        """
        调用 LLM 进行能力分析

        Args:
            content_obj: Worker Profile 内容对象
            bot_cognition: Bot 认知摘要（可选，用于增强经验能力分析）

        Returns:
            ProfileAnalysisResult: 分析结果
        """

        # 获取显示名称和描述
        display_name = content_obj.display_name or content_obj.worker_id
        description = content_obj.description or "无"

        # 构建各部分内容（使用代码块包裹避免 markdown 层级混乱）
        def format_section(title: str, content: Optional[str]) -> str:
            if content:
                return f"## {title}\n```\n{content}\n```"
            return f"## {title}\n无"

        soul_md_section = format_section("soul.md", content_obj.soul_md)

        contents_map = content_obj.contents
        identity_md_section = format_section("identity.md", contents_map.get("identity.md"))
        memory_md_section = format_section("memory.md", contents_map.get("memory.md"))

        # Bot 认知信息（优先使用传入的 bot_cognition，否则从 contents 中取）
        cognition_content = bot_cognition or contents_map.get("bot_cognition_info")
        bot_cognition_section = format_section("bot_cognition_info", cognition_content) if cognition_content else ""

        # 将 SkillSet 对象转换为可序列化的 dict 列表
        skill_sets_data = [skill.model_dump(mode='json') for skill in content_obj.skill_sets]
        skills_list_section = format_section("skill_list", json.dumps(skill_sets_data, ensure_ascii=False))

        user_prompt = USER_PROMPT_TEMPLATE.format(
            display_name=display_name,
            description=description,
            soul_md_section=soul_md_section,
            identity_md_section=identity_md_section,
            memory_md_section=memory_md_section,
            bot_cognition_section=bot_cognition_section,
            skills_list_section=skills_list_section,
        )

        task_spec = LLMTaskSpec(
            task_type=TaskType.PROFILE_ANALYSIS,
            complexity=Complexity.MEDIUM,
            need_structured_output=True,
            cost_sensitivity=CostSensitivity.MEDIUM,
            latency_budget_ms=30000,
        )

        request = LLMRequest(
            task_spec=task_spec,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=8192,
            metadata=None,
        )

        try:
            response = self._gateway.generate(request)

            # 记录详细的响应信息用于诊断
            logger.info(
                f"[ProfileAnalyzer] LLM 响应: "
                f"parse_success={response.parse_success}, "
                f"raw_text_len={len(response.raw_text) if response.raw_text else 0}, "
                f"errors={response.errors}, "
                f"warnings={response.warnings}, "
                f"finish_reason={response.finish_reason}, "
                f"latency_ms={response.latency_ms}"
            )

            # 尝试从 raw_text 解析
            if response.raw_text:
                # 调试：打印 raw_text 的前 2000 字符
                logger.info(f"[ProfileAnalyzer] raw_text preview (first 2000 chars):\n{response.raw_text[:2000]}")
                return self._parse_raw_response(response.raw_text)

            # 诊断空响应
            diag_parts = []
            if response.errors:
                diag_parts.append(f"errors={response.errors}")
            if response.warnings:
                diag_parts.append(f"warnings={response.warnings}")
            diag_msg = ", ".join(diag_parts) if diag_parts else "unknown reason"
            logger.warning(f"[ProfileAnalyzer] LLM 返回空内容: {diag_msg}")
            return ProfileAnalysisResult(
                semantic_profile=None,
                capability_tags=[],
                llm_success=False,
                error_message=f"LLM returned empty content: {diag_msg}",
            )

        except Exception as e:
            logger.warning(f"[ProfileAnalyzer] LLM 调用异常: {e}")
            return ProfileAnalysisResult(
                semantic_profile=None,
                capability_tags=[],
                llm_success=False,
                error_message=str(e),
            )

    def _parse_raw_response(self, raw_text: str) -> ProfileAnalysisResult:
        """
        解析 LLM 返回的原始 Markdown 文本

        从 raw_text 中提取:
        - short_profile: 一句话简介（30字以内）
        - semantic_profile: 职责定位 + 经验能力 + SKILL能力 的组合
        - capability_tags: 能力标签列表

        Args:
            raw_text: LLM 返回的原始 Markdown 文本

        Returns:
            ProfileAnalysisResult
        """
        try:
            # 提取一句话简介（30字以内）
            short_profile = self._extract_short_profile(raw_text)

            # 提取能力标签（从 "## 能力标签" 部分）
            capability_tags = self._extract_capability_tags(raw_text)

            # 构建 semantic_profile（组合职责定位、经验能力、SKILL能力）
            semantic_profile = self._build_semantic_profile(raw_text)

            logger.info(
                f"[ProfileAnalyzer] 解析成功: tags={capability_tags}, "
                f"short_profile_len={len(short_profile) if short_profile else 0}, "
                f"profile_len={len(semantic_profile) if semantic_profile else 0}"
            )

            return ProfileAnalysisResult(
                semantic_profile=semantic_profile,
                capability_tags=capability_tags,
                llm_success=True,
                short_profile=short_profile,
            )

        except Exception as e:
            logger.warning(f"[ProfileAnalyzer] 解析原始响应失败: {e}")
            return ProfileAnalysisResult(
                semantic_profile=None,
                capability_tags=[],
                llm_success=False,
                short_profile=None,
                error_message=f"Failed to parse raw response: {e}",
            )

    def _extract_capability_tags(self, raw_text: str) -> list[str]:
        """
        从 raw_text 中提取能力标签

        查找 "## 能力标签" 或 "##能力标签" 部分，
        匹配 "能力标签: xxx, yyy, zzz" 或类似格式

        Args:
            raw_text: LLM 返回的原始文本

        Returns:
            能力标签列表
        """
        tags = []

        # 先提取整个 "## 能力标签" 或 "## 标签列表" section
        section_pattern = r"##\s*(?:能力标签|标签列表)\s*\n+(.*?)(?=\n##\s|\Z)"
        section_match = re.search(section_pattern, raw_text, re.DOTALL | re.IGNORECASE)

        if not section_match:
            return tags

        section_content = section_match.group(1).strip()

        # 移除 yaml 代码块标记
        section_content = re.sub(r"^```(?:yaml)?\s*\n?", "", section_content)
        section_content = re.sub(r"\n?```\s*$", "", section_content)
        section_content = section_content.strip()

        # 如果内容为空，返回空列表
        if not section_content:
            return tags

        # 尝试匹配 "能力标签:" 前缀，如果有则取后面的内容
        prefix_match = re.match(r"^标签列表[:：]\s*", section_content)
        if prefix_match:
            section_content = section_content[prefix_match.end():].strip()

        # 如果内容为空，返回空列表
        if not section_content:
            return tags

        # 按逗号或顿号分隔，过滤空字符串和纯标记符号
        tags = [
            t.strip()
            for t in re.split(r"[,，、\n]", section_content)
            if t.strip() and t.strip() not in ["能力标签:", "能力标签："]
        ]

        return tags

    def _build_semantic_profile(self, raw_text: str) -> Optional[str]:
        """
        从 raw_text 构建 semantic_profile

        组合三部分:
        1. 职责定位（身份 + 目标）
        2. 经验能力（核心能力）
        3. SKILL能力（技能）

        Args:
            raw_text: LLM 返回的原始文本

        Returns:
            组合后的语义画像字符串
        """
        sections = []

        # 提取职责定位
        identity_section = self._extract_section(raw_text, "职责定位")
        if identity_section:
            sections.append(f"【职责定位】\n{identity_section}")

        # 提取经验能力
        experience_section = self._extract_section(raw_text, "经验能力")
        if experience_section:
            sections.append(f"【经验能力】\n{experience_section}")

        # 提取 Skill能力
        skill_section = self._extract_section(raw_text, "[Ss]kill能力")
        if skill_section:
            sections.append(f"【SKILL能力】\n{skill_section}")

        return "\n\n".join(sections) if sections else None

    def _extract_section(self, raw_text: str, section_pattern: str) -> Optional[str]:
        """
        提取指定 section 的内容

        Args:
            raw_text: 原始文本
            section_pattern: section 标题的正则模式

        Returns:
            section 内容，如果未找到返回 None
        """
        # 匹配 "## section_name" 到下一个 "##" 或文本结束
        pattern = rf"##\s*{section_pattern}\s*\n+(.*?)(?=\n##\s|\Z)"
        match = re.search(pattern, raw_text, re.DOTALL | re.IGNORECASE)

        if match:
            content = match.group(1).strip()
            # 清理内容：移除首尾空白和多余的空行
            content = re.sub(r"\n{3,}", "\n\n", content)
            return content if content else None

        return None

    def _extract_short_profile(self, raw_text: str) -> Optional[str]:
        """
        提取一句话简介（不超过30字）

        查找 "## 一句话简介" 部分，返回精简描述。
        如果超过30字，自动截断。

        Args:
            raw_text: LLM 返回的原始文本

        Returns:
            30字以内的一句话简介，或 None
        """
        section = self._extract_section(raw_text, "一句话简介")
        if section:
            # 清理并截断到30字
            short = section.strip().replace("\n", " ")
            # 移除可能的 "- " 前缀
            short = re.sub(r"^[-・]\s*", "", short)
            if len(short) > 30:
                short = short[:30] + "..."
            return short if short else None
        return None

    def _parse_structured_response(self, data: dict) -> ProfileAnalysisResult:
        """
        解析结构化 LLM 响应

        Args:
            data: 结构化数据

        Returns:
            ProfileAnalysisResult
        """
        semantic_profile = data.get("semantic_profile")
        capability_tags = data.get("capability_tags", [])


        logger.info(
            f"[ProfileAnalyzer] 解析成功: tags={capability_tags}, "
            f"profile_len={len(semantic_profile) if semantic_profile else 0}"
        )

        return ProfileAnalysisResult(
            semantic_profile=semantic_profile,
            capability_tags=capability_tags,
            llm_success=True,
        )



__all__ = [
    "ProfileAnalyzerService",
    "ProfileAnalysisResult",
]