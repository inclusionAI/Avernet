"""
FakeLLMProvider

LLM Gateway / Provider Layer

用于开发和测试的 Fake LLM Provider。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

from src.domain.services.llm_provider import LLMProvider
from src.domain.models.llm_request import LLMRequest
from src.domain.models.llm_response import (
    LLMResponse,
    LLMUsage,
    LLMError,
    FinishReason,
)
from src.domain.models.llm_task_spec import TaskType


# 默认的 Fusion Recommendation 响应模板
DEFAULT_FUSION_RECOMMENDATION: dict[str, Any] = {
    "summary": "基于多个视角的综合分析，方案整体可行，但需要关注部分风险点。",
    "decision": "conditional_yes",
    "reasoning": [
        "多数参与者认为方案可行",
        "存在部分需要补充的内容",
    ],
    "risks": [
        "安全视角需要补充审计日志",
        "运维角度需要补充监控方案",
    ],
    "missing_information": [],
    "next_actions": [
        "补充安全审计方案",
        "添加监控告警配置",
    ],
    "confidence": 0.75,
}


class FakeLLMProvider(LLMProvider):
    """
    Fake LLM Provider

    用于开发和测试的 fake provider。

    特性：
    - 返回固定的模拟响应
    - 支持自定义响应列表
    - 支持解析失败模拟
    - 支持超时/错误模拟

    Attributes:
        responses: 自定义响应列表（循环使用）
        simulate_parse_failure: 是否模拟解析失败
        simulate_timeout: 是否模拟超时
        simulate_error: 是否模拟错误
        latency_ms: 模拟延迟（毫秒）
    """

    def __init__(
        self,
        responses: Optional[list[dict[str, Any]]] = None,
        simulate_parse_failure: bool = False,
        simulate_timeout: bool = False,
        simulate_error: bool = False,
        latency_ms: int = 10,
    ):
        """
        初始化 Fake Provider

        Args:
            responses: 自定义响应列表
            simulate_parse_failure: 是否模拟解析失败
            simulate_timeout: 是否模拟超时
            simulate_error: 是否模拟错误
            latency_ms: 模拟延迟（毫秒）
        """
        self._responses = responses or []
        self._simulate_parse_failure = simulate_parse_failure
        self._simulate_timeout = simulate_timeout
        self._simulate_error = simulate_error
        self._latency_ms = latency_ms
        self._request_count = 0

    def generate(
        self,
        request: LLMRequest,
        model: Optional[str] = None,
    ) -> LLMResponse:
        """
        执行 LLM 生成请求

        Args:
            request: LLM 请求对象
            model: 物理模型名称（可选，fake provider 忽略此参数）

        Returns:
            LLMResponse: LLM 响应对象
        """
        start_time = time.time()

        # 模拟延迟
        if self._latency_ms > 0:
            time.sleep(self._latency_ms / 1000.0)

        # 模拟超时
        if self._simulate_timeout:
            return LLMResponse(
                provider_id="fake",
                model_id="fake-model",
                raw_text="",
                parse_success=False,
                latency_ms=int((time.time() - start_time) * 1000),
                finish_reason=FinishReason.ERROR,
                errors=[
                    LLMError(
                        code="TIMEOUT_ERROR",
                        message="Request timed out",
                    )
                ],
            )

        # 模拟错误
        if self._simulate_error:
            return LLMResponse(
                provider_id="fake",
                model_id="fake-model",
                raw_text="",
                parse_success=False,
                latency_ms=int((time.time() - start_time) * 1000),
                finish_reason=FinishReason.ERROR,
                errors=[
                    LLMError(
                        code="PROVIDER_ERROR",
                        message="Provider encountered an error",
                    )
                ],
            )

        # 模拟解析失败 - 返回无效 JSON
        if self._simulate_parse_failure and request.expected_schema_name:
            return LLMResponse(
                provider_id="fake",
                model_id="fake-model",
                raw_text="This is not valid JSON output from LLM",
                parse_success=False,
                latency_ms=int((time.time() - start_time) * 1000),
                finish_reason=FinishReason.STOP,
                warnings=["Simulated parse failure"],
            )

        # 获取响应
        raw_text, structured_data = self._get_response_content(request)

        # 计算延迟
        latency_ms = int((time.time() - start_time) * 1000)

        # 构建 usage
        usage = LLMUsage(
            input_tokens=len(request.user_prompt.split()) + 10,
            output_tokens=len(raw_text.split()) + 5,
            total_tokens=len(request.user_prompt.split()) + len(raw_text.split()) + 15,
        )

        return LLMResponse(
            provider_id="fake",
            model_id="fake-model",
            raw_text=raw_text,
            structured_data=structured_data,
            parse_success=True,
            latency_ms=latency_ms,
            usage=usage,
            finish_reason=FinishReason.STOP,
            warnings=[],
        )

    def _get_response_content(
        self,
        request: LLMRequest,
    ) -> tuple[str, Optional[dict[str, Any]]]:
        """
        获取响应内容

        Args:
            request: LLM 请求

        Returns:
            tuple: (raw_text, structured_data)
        """
        # 使用自定义响应
        if self._responses:
            response = self._responses[self._request_count % len(self._responses)]
            self._request_count += 1
            return response.get("raw_text", ""), response.get("structured_data")

        # 根据任务类型生成默认响应
        if request.task_spec.task_type == TaskType.FUSION_RECOMMENDATION:
            structured_data = DEFAULT_FUSION_RECOMMENDATION.copy()
            raw_text = json.dumps(structured_data, ensure_ascii=False)
            return raw_text, structured_data

        if request.task_spec.task_type == TaskType.SUMMARY:
            raw_text = "这是一个测试摘要响应。"
            return raw_text, None

        if request.task_spec.task_type == TaskType.EXTRACTION:
            structured_data = {"entities": [], "relations": []}
            raw_text = json.dumps(structured_data, ensure_ascii=False)
            return raw_text, structured_data

        # 默认响应
        raw_text = f"Fake response for task type: {request.task_spec.task_type.value}"
        return raw_text, None


__all__ = [
    "FakeLLMProvider",
    "DEFAULT_FUSION_RECOMMENDATION",
]