"""
FusionRecommendationService

LLM Gateway / Provider Layer

Fusion Recommendation 服务，负责生成 G1 融合建议。
"""

from __future__ import annotations

from typing import Optional

from src.application.services.llm_gateway_service import LLMGatewayService
from src.domain.models.llm_request import LLMRequest
from src.domain.models.llm_task_spec import LLMTaskSpec, TaskType
from src.domain.models.fusion_result import Perspective
from src.domain.models.fusion_recommendation import FusionRecommendation, Decision
from src.infra.llm.prompts.fusion_recommendation_prompt import build_fusion_recommendation_prompt
from src.infra.llm.parsing.structured_output_parser import StructuredOutputParser


class FusionRecommendationService:
    """
    Fusion Recommendation 服务

    负责基于 G1 Fusion 的输入生成结构化建议。

    Attributes:
        gateway: LLM Gateway 服务
    """

    def __init__(self, gateway: LLMGatewayService):
        """
        初始化服务

        Args:
            gateway: LLM Gateway 服务
        """
        self._gateway = gateway

    def generate(
        self,
        question: str,
        driver_bot_id: Optional[str],
        perspectives: list[Perspective],
        partial_success: bool = False,
        warnings: Optional[list[str]] = None,
        errors: Optional[list[str]] = None,
    ) -> FusionRecommendation:
        """
        生成融合建议

        Args:
            question: 问题
            driver_bot_id: Driver bot ID
            perspectives: 视角列表
            partial_success: 是否部分成功
            warnings: 警告列表
            errors: 错误列表

        Returns:
            FusionRecommendation: 融合建议
        """
        # 构建 Prompt
        perspectives_data = [p.model_dump() for p in perspectives]
        system_prompt, user_prompt = build_fusion_recommendation_prompt(
            question=question,
            driver_bot_id=driver_bot_id,
            perspectives=perspectives_data,
            partial_success=partial_success,
            warnings=warnings,
            errors=errors,
        )

        # 创建请求
        task_spec = LLMTaskSpec(
            task_type=TaskType.FUSION_RECOMMENDATION,
            need_structured_output=True,
            require_explanation=True,
        )

        request = LLMRequest(
            task_spec=task_spec,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            expected_schema_name="FusionRecommendation",
            temperature=0.2,
            max_tokens=4096,  # 提升 to 4096，避免 GLM-5 输出被截断导致 JSON 解析失败
        )

        # 调用 Gateway
        response = self._gateway.generate(request)

        # 处理响应
        if response.parse_success and response.structured_data:
            # 解析成功，构造 FusionRecommendation
            try:
                return FusionRecommendation.model_validate(response.structured_data)
            except Exception:
                # 验证失败，返回默认值
                pass

        # 解析失败或无结构化数据，返回基于规则的默认建议
        return self._generate_fallback_recommendation(
            question=question,
            perspectives=perspectives,
            partial_success=partial_success,
            warnings=warnings,
        )

    def _generate_fallback_recommendation(
        self,
        question: str,
        perspectives: list[Perspective],
        partial_success: bool,
        warnings: Optional[list[str]] = None,
    ) -> FusionRecommendation:
        """
        生成备用建议（当 LLM 调用失败时）

        Args:
            question: 问题
            perspectives: 视角列表
            partial_success: 是否部分成功
            warnings: 警告列表

        Returns:
            FusionRecommendation: 基于规则的融合建议
        """
        # 统计完成和失败的视角
        completed = [p for p in perspectives if p.status == "completed"]
        failed = [p for p in perspectives if p.status in ("failed", "timed_out", "skipped")]

        # 计算平均置信度
        confidences = [p.confidence for p in completed if p.confidence is not None]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.5

        # 确定决策
        if len(failed) == 0 and avg_confidence >= 0.8:
            decision = Decision.YES
            summary = "各方视角一致认为方案可行。"
        elif len(failed) == 0 and avg_confidence >= 0.6:
            decision = Decision.CONDITIONAL_YES
            summary = "方案基本可行，但存在部分顾虑需要关注。"
        elif len(completed) > 0:
            decision = Decision.CONDITIONAL_YES
            summary = f"基于 {len(completed)} 个成功视角，方案可推进，但需补齐缺失视角。"
        else:
            decision = Decision.NEEDS_MORE_INFORMATION
            summary = "信息不足，无法做出判断。"

        # 生成摘要
        if completed:
            first_summary = completed[0].summary[:50] if len(completed[0].summary) > 50 else completed[0].summary
            summary = f"综合 {len(completed)} 个视角：{completed[0].participant_id}: {first_summary}"

        # 汇总风险
        risks: list[str] = []
        for p in failed:
            risks.append(f"{p.participant_id} 视角缺失")

        # 汇总下一步行动
        next_actions: list[str] = []
        for p in perspectives:
            if p.status == "completed" and p.summary:
                if "需要" in p.summary or "建议" in p.summary or "补充" in p.summary:
                    next_actions.append(f"跟进 {p.participant_id} 的建议")

        # 缺失信息
        missing_information: list[str] = []
        if partial_success:
            missing_information.append("部分参与者视角缺失")
        if warnings:
            missing_information.extend(warnings)

        return FusionRecommendation(
            summary=summary,
            decision=decision,
            reasoning=[f"基于 {len(completed)} 个完成的视角进行分析"],
            risks=risks,
            missing_information=missing_information,
            next_actions=next_actions,
            confidence=avg_confidence,
        )


__all__ = [
    "FusionRecommendationService",
]