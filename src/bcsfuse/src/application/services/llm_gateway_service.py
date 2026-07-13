"""
LLMGatewayService

LLM Gateway / Provider Layer

LLM Gateway 服务，协调 Provider、Router 和 Parser。

Stage 4 Phase 4 增强:
- 支持 parse failure 时的单次重试
- 详尽的日志记录
"""

from __future__ import annotations

import logging
from typing import Optional

from src.domain.services.llm_provider import LLMProvider
from src.domain.services.llm_router import LLMRouter
from src.domain.models.llm_request import LLMRequest
from src.domain.models.llm_response import LLMResponse, FinishReason
from src.infra.llm.config.llm_settings import LLMSettings
from src.infra.llm.routing.static_llm_router import StaticLLMRouter
from src.infra.llm.parsing.structured_output_parser import StructuredOutputParser

logger = logging.getLogger(__name__)


class LLMGatewayService:
    """
    LLM Gateway 服务

    协调 LLM Provider、Router 和 Parser，提供统一的 LLM 调用接口。

    Attributes:
        provider: LLM Provider
        router: LLM Router
        settings: LLM 配置
    """

    def __init__(
        self,
        provider: LLMProvider,
        router: Optional[LLMRouter] = None,
        settings: Optional[LLMSettings] = None,
    ):
        """
        初始化 Gateway 服务

        Args:
            provider: LLM Provider
            router: LLM Router（可选，默认使用 StaticLLMRouter）
            settings: LLM 配置（可选）
        """
        self._provider = provider
        self._router = router or StaticLLMRouter(settings)
        self._settings = settings or LLMSettings()

    def generate(self, request: LLMRequest, max_retries: int = 1) -> LLMResponse:
        """
        执行 LLM 生成请求

        Args:
            request: LLM 请求
            max_retries: 最大重试次数（仅当 parse failure 时重试）

        Returns:
            LLMResponse: LLM 响应
        """
        logger.info("[LLM-GATEWAY] ========== generate 开始 ==========")
        logger.info("[LLM-GATEWAY]   expected_schema_name: %s", request.expected_schema_name)
        logger.info("[LLM-GATEWAY]   need_structured_output: %s", request.task_spec.need_structured_output)
        logger.info("[LLM-GATEWAY]   max_retries: %d", max_retries)

        # 使用 Router 路由任务获取模型
        model_profile = self._router.route(request.task_spec)
        physical_model = model_profile.physical_model_name
        logger.info("[LLM-GATEWAY]   physical_model: %s", physical_model)

        # 调用 Provider（传入物理模型名称）
        logger.info("[LLM-GATEWAY] 调用 _provider.generate()...")
        response = self._provider.generate(request, model=physical_model)
        logger.info("[LLM-GATEWAY]   response.provider_id: %s", response.provider_id)
        logger.info("[LLM-GATEWAY]   response.latency_ms: %d", response.latency_ms)
        logger.info("[LLM-GATEWAY]   response.finish_reason: %s", response.finish_reason)

        # 如果需要结构化输出，进行解析
        if request.expected_schema_name and response.raw_text:
            logger.info("[LLM-GATEWAY] 开始解析结构化输出...")
            parse_result = StructuredOutputParser.parse(
                response.raw_text,
                request.expected_schema_name,
            )

            if parse_result.success:
                logger.info("[LLM-GATEWAY] ✅ 解析成功")
                response = LLMResponse(
                    provider_id=response.provider_id,
                    model_id=response.model_id,
                    raw_text=response.raw_text,
                    structured_data=parse_result.data,
                    parse_success=True,
                    latency_ms=response.latency_ms,
                    usage=response.usage,
                    finish_reason=response.finish_reason,
                    warnings=response.warnings + parse_result.warnings,
                    errors=response.errors,
                )
            else:
                # 解析失败
                logger.warning("[LLM-GATEWAY] ⚠️ 解析失败: %s", parse_result.error_message)

                # 尝试重试（仅一次）
                if max_retries > 0:
                    logger.info("[LLM-GATEWAY] 尝试重试 LLM 调用 (剩余重试次数: %d)...", max_retries - 1)

                    retry_response = self._provider.generate(request, model=physical_model)
                    logger.info("[LLM-GATEWAY]   重试完成, latency_ms: %d", retry_response.latency_ms)

                    if retry_response.raw_text:
                        retry_parse_result = StructuredOutputParser.parse(
                            retry_response.raw_text,
                            request.expected_schema_name,
                        )

                        if retry_parse_result.success:
                            logger.info("[LLM-GATEWAY] ✅ 重试解析成功")
                            return LLMResponse(
                                provider_id=retry_response.provider_id,
                                model_id=retry_response.model_id,
                                raw_text=retry_response.raw_text,
                                structured_data=retry_parse_result.data,
                                parse_success=True,
                                latency_ms=response.latency_ms + retry_response.latency_ms,
                                usage=retry_response.usage,
                                finish_reason=retry_response.finish_reason,
                                warnings=retry_response.warnings + retry_parse_result.warnings,
                                errors=[],
                            )
                        else:
                            logger.warning("[LLM-GATEWAY] ❌ 重试解析仍失败: %s", retry_parse_result.error_message)

                # 重试后仍然失败，返回失败响应
                logger.warning("[LLM-GATEWAY] ❌ 最终解析失败，返回 parse_success=False")
                response = LLMResponse(
                    provider_id=response.provider_id,
                    model_id=response.model_id,
                    raw_text=response.raw_text,
                    structured_data=None,
                    parse_success=False,
                    latency_ms=response.latency_ms,
                    usage=response.usage,
                    finish_reason=response.finish_reason,
                    warnings=response.warnings + [parse_result.error_message or "Parse failed"],
                    errors=response.errors,
                )

        logger.info("[LLM-GATEWAY] ========== generate 完成 ==========")
        return response

    @property
    def provider(self) -> LLMProvider:
        """获取 Provider"""
        return self._provider

    @property
    def router(self) -> LLMRouter:
        """获取 Router"""
        return self._router


__all__ = [
    "LLMGatewayService",
]