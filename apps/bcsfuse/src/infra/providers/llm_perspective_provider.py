"""
LLMPerspectiveProvider

G1/G2: LLM-based Perspective Provider

基于 LLM 的视角生成器，使用 Worker Profile 和 LLM 生成真实的专家视角。

职责：
- 从 Profile Source 获取 Worker Profile 内容
- 构建 Perspective Prompt
- 调用 LLM Gateway 生成视角
- 解析 LLM 响应并返回 Perspective

与 StubPerspectiveProvider 的区别：
- StubPerspectiveProvider: 返回固定的或空洞的响应（开发测试用）
- LLMPerspectiveProvider: 调用真实 LLM 生成有意义的视角
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from src.domain.services.perspective_provider import PerspectiveProvider, PerspectiveContext
from src.domain.models.fusion_result import Perspective
from src.domain.models.llm_request import LLMRequest
from src.domain.models.llm_task_spec import LLMTaskSpec, TaskType

if TYPE_CHECKING:
    from src.application.services.llm_gateway_service import LLMGatewayService
    from src.domain.services.worker_profile_source import WorkerProfileSource
    from src.domain.models.worker_profile import WorkerProfile

logger = logging.getLogger(__name__)


# System Prompt for Perspective Generation
PERSPECTIVE_SYSTEM_PROMPT = """你是一个"专业视角生成器"。

你的任务是基于给定的专家画像和问题，生成一个专业、有深度的视角。

你必须遵守以下规则：

1. 必须基于输入的专家画像内容生成视角，不得虚构专家未提及的技能或经验。
2. 生成的视角必须具有实质内容，包括：
   - summary: 针对问题的专业见解摘要
   - key_points: 核心观点列表（至少2个）
   - concerns: 主要顾虑列表（至少1个）
   - evidence: 支持观点的证据或依据
3. 视角应该体现专家的专业背景和决策风格。
4. 如果信息不足以形成完整的专家视角，应该在 concerns 中说明，并适当降低 confidence。
5. 输出必须是严格的 JSON 格式。

置信度(confidence)指南：
- 0.9-1.0: 有充分的专业背景支持，观点明确
- 0.7-0.9: 有相关的专业背景，但需要更多信息确认
- 0.5-0.7: 专业背景不太相关，或信息不足
- 0.3-0.5: 专业背景相关性低，观点谨慎"""


# User Prompt Template
PERSPECTIVE_USER_PROMPT_TEMPLATE = """请基于以下专家画像，生成专业视角 JSON。

[问题]
{question}

[专家标识]
{expert_id}

[专家画像内容]
{profile_content}

请输出一个严格 JSON 对象，字段必须完全符合以下要求：
{{
  "summary": "专业视角摘要（100-300字）",
  "confidence": 0.0-1.0之间的浮点数,
  "key_points": ["核心观点1", "核心观点2", ...],
  "concerns": ["主要顾虑1", "主要顾虑2", ...],
  "evidence": ["支持证据1", "支持证据2", ...]
}}

要求：
- key_points 至少包含2个观点
- concerns 至少包含1个顾虑
- summary 必须体现专家的专业背景
- 不要输出任何 JSON 之外的文字"""


class LLMPerspectiveProvider:
    """
    LLM-based Perspective Provider

    使用 LLM 和 Worker Profile 生成真实的专业视角。

    Attributes:
        _gateway: LLM Gateway 服务
        _profile_source: Worker Profile 来源
    """

    def __init__(
        self,
        gateway: "LLMGatewayService",
        profile_source: "WorkerProfileSource",
    ):
        """
        初始化 LLM Perspective Provider

        Args:
            gateway: LLM Gateway 服务
            profile_source: Worker Profile 来源
        """
        self._gateway = gateway
        self._profile_source = profile_source
        logger.info("[LLM-Perspective] Provider initialized")

    def collect(self, context: PerspectiveContext) -> Perspective:
        """
        收集单个 participant 的视角

        Args:
            context: 视角收集上下文

        Returns:
            Perspective: 收集到的视角
        """
        participant_id = context.participant_id
        logger.info("[LLM-Perspective] ========== collect 开始 ==========")
        logger.info("[LLM-Perspective] participant_id: %s", participant_id)
        logger.info("[LLM-Perspective] question: %s", context.question[:80] if len(context.question) > 80 else context.question)

        # Phase D2: Initialize diagnostics metadata
        diagnostics = {
            "profile_loaded": False,
            "profile_content_length": 0,
            "profile_format": None,
            "profile_format_conversion_success": False,
            "llm_called": False,
            "llm_success": False,
            "fallback_used": False,
            "fallback_reason": None,
        }

        # Step 1: 解析 participant_id 获取 staff_id 和 profile_id
        staff_id, profile_id = self._parse_participant_id(participant_id)
        logger.info("[LLM-Perspective] staff_id=%s, profile_id=%s", staff_id, profile_id)

        # Step 2: 获取 Profile 内容
        profile_content, profile_diagnostics = self._get_profile_content_with_diagnostics(staff_id, profile_id)
        diagnostics.update(profile_diagnostics)

        if not profile_content:
            logger.warning("[LLM-Perspective] Profile not found, returning fallback perspective")
            diagnostics["fallback_used"] = True
            diagnostics["fallback_reason"] = "Profile not found"
            perspective = self._create_fallback_perspective(context, "Profile not found")
            perspective.metadata["diagnostics"] = diagnostics
            return perspective

        # Step 3: 构建 Prompt
        user_prompt = PERSPECTIVE_USER_PROMPT_TEMPLATE.format(
            question=context.question,
            expert_id=participant_id,
            profile_content=profile_content[:3000],  # 限制长度避免超出 token 限制
        )

        logger.info("[LLM-Perspective] Prompt 构建完成，profile_content 长度: %d", len(profile_content))

        # Step 4: 调用 LLM
        try:
            task_spec = LLMTaskSpec(
                task_type=TaskType.FUSION_RECOMMENDATION,  # 复用该类型
                need_structured_output=True,
                require_explanation=False,
            )

            request = LLMRequest(
                task_spec=task_spec,
                system_prompt=PERSPECTIVE_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                expected_schema_name="Perspective",
                temperature=0.3,
                max_tokens=4096,  # 增加 token 限制，避免响应被截断
            )

            logger.info("[LLM-Perspective] 调用 LLM Gateway...")
            diagnostics["llm_called"] = True
            response = self._gateway.generate(request)
            logger.info("[LLM-Perspective] LLM 响应完成, parse_success=%s", response.parse_success)

            # Step 5: 解析响应
            if response.parse_success and response.structured_data:
                perspective = self._parse_llm_response(participant_id, response.structured_data)
                diagnostics["llm_success"] = True
                perspective.metadata["diagnostics"] = diagnostics
                logger.info("[LLM-Perspective] ✅ 视角生成成功")
                return perspective
            else:
                logger.warning("[LLM-Perspective] LLM 解析失败，返回 fallback")
                diagnostics["fallback_used"] = True
                diagnostics["fallback_reason"] = "LLM parse failed"
                perspective = self._create_fallback_perspective(context, "LLM parse failed")
                perspective.metadata["diagnostics"] = diagnostics
                return perspective

        except Exception as e:
            logger.error("[LLM-Perspective] ❌ LLM 调用失败: %s", str(e))
            diagnostics["fallback_used"] = True
            diagnostics["fallback_reason"] = f"LLM error: {str(e)}"
            perspective = self._create_fallback_perspective(context, f"LLM error: {str(e)}")
            perspective.metadata["diagnostics"] = diagnostics
            return perspective

    def _parse_participant_id(self, participant_id: str) -> tuple[str, str]:
        """
        解析 participant_id 获取 staff_id 和 profile_id

        Args:
            participant_id: 格式为 "{worker_id}:{profile_id}" 或单独的 worker_id

        Returns:
            tuple: (staff_id, profile_id)
        """
        if ":" in participant_id:
            parts = participant_id.split(":", 1)
            return parts[0], parts[1]
        else:
            # 只有 worker_id，使用 default profile
            return participant_id, "default"

    def _get_profile_content(self, staff_id: str, profile_id: str) -> Optional[str]:
        """
        获取 Profile 内容

        Args:
            staff_id: 员工/Worker ID
            profile_id: Profile ID

        Returns:
            Profile 内容字符串，或 None
        """
        content, _ = self._get_profile_content_with_diagnostics(staff_id, profile_id)
        return content

    def _get_profile_content_with_diagnostics(self, staff_id: str, profile_id: str) -> tuple[Optional[str], dict]:
        """
        获取 Profile 内容（带诊断信息）

        Phase D2: 返回 profile content 和 diagnostics metadata

        Args:
            staff_id: 员工/Worker ID
            profile_id: Profile ID

        Returns:
            tuple: (Profile 内容字符串, diagnostics dict)
        """
        diagnostics = {
            "profile_loaded": False,
            "profile_content_length": 0,
            "profile_format": None,
            "profile_format_conversion_success": False,
        }

        try:
            profile = self._profile_source.get_profile(staff_id, profile_id)
            if profile is None:
                logger.warning("[LLM-Perspective] Profile not found: staff_id=%s, profile_id=%s", staff_id, profile_id)
                diagnostics["profile_format_conversion_success"] = False
                return None, diagnostics

            # Phase D2: Check if profile came from normalization
            # APIProfileSource sets metadata on WorkerProfile objects
            if hasattr(profile, 'metadata') and isinstance(profile.metadata, dict):
                # Check for normalization diagnostics from APIProfileSource
                if 'profile_format' in profile.metadata:
                    diagnostics["profile_format"] = profile.metadata['profile_format']
                if 'profile_format_conversion_success' in profile.metadata:
                    diagnostics["profile_format_conversion_success"] = profile.metadata['profile_format_conversion_success']
                if 'normalized_content_length' in profile.metadata:
                    diagnostics["profile_content_length"] = profile.metadata['normalized_content_length']

            # 提取可搜索文本作为内容
            content_parts = []

            if profile.searchable_text:
                content_parts.append(f"核心能力摘要:\n{profile.searchable_text}")

            # 添加上下文片段
            if profile.context_fragments:
                for i, fragment in enumerate(profile.context_fragments[:5]):  # 限制片段数量
                    if fragment.content:
                        fragment_type = fragment.kind.value if hasattr(fragment, 'kind') else 'unknown'
                        content_parts.append(f"\n上下文片段 {i+1} ({fragment_type}):\n{fragment.content[:500]}")

            if not content_parts:
                logger.warning("[LLM-Perspective] Profile has no content: staff_id=%s", staff_id)
                diagnostics["profile_format_conversion_success"] = False
                return None, diagnostics

            content = "\n".join(content_parts)
            diagnostics["profile_loaded"] = True
            diagnostics["profile_content_length"] = len(content)

            # If format conversion success not set yet, assume success if content exists
            if "profile_format_conversion_success" not in diagnostics or diagnostics["profile_format_conversion_success"] is False:
                diagnostics["profile_format_conversion_success"] = len(content) > 0

            # If format not set, check if profile has soul_md or came from dict
            if diagnostics["profile_format"] is None:
                if hasattr(profile, 'soul_md') and profile.soul_md:
                    diagnostics["profile_format"] = "object_worker_profile_content"
                else:
                    diagnostics["profile_format"] = "unknown"

            return content, diagnostics

        except Exception as e:
            logger.error("[LLM-Perspective] Failed to get profile: %s", str(e))
            diagnostics["profile_format_conversion_success"] = False
            return None, diagnostics

    def _parse_llm_response(self, participant_id: str, data: dict) -> Perspective:
        """
        解析 LLM 响应为 Perspective

        Args:
            participant_id: 参与者 ID
            data: LLM 返回的结构化数据

        Returns:
            Perspective 对象
        """
        return Perspective(
            participant_id=participant_id,
            participant_type="bot",
            role="consultant",
            summary=data.get("summary", ""),
            confidence=data.get("confidence", 0.7),
            evidence=data.get("evidence", []),
            status="completed",
            key_points=data.get("key_points", []),
            concerns=data.get("concerns", []),
        )

    def _create_fallback_perspective(self, context: PerspectiveContext, reason: str) -> Perspective:
        """
        创建备用视角（当 LLM 调用失败时）

        Args:
            context: 视角收集上下文
            reason: 失败原因

        Returns:
            Perspective 对象
        """
        return Perspective(
            participant_id=context.participant_id,
            participant_type="bot",
            role="consultant",
            summary=f"无法生成专业视角（原因：{reason}）。请检查 Profile 配置或稍后重试。",
            confidence=0.3,
            evidence=[],
            status="completed",
            key_points=[],
            concerns=[f"视角生成失败: {reason}"],
        )


__all__ = [
    "LLMPerspectiveProvider",
    "PERSPECTIVE_SYSTEM_PROMPT",
    "PERSPECTIVE_USER_PROMPT_TEMPLATE",
]