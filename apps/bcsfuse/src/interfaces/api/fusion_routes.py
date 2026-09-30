"""
Fusion API Routes

G1: Fusion Entry Layer / G2: Conflict Alignment Layer / G5: Expert Diagnosis Layer

Fusion 相关的 HTTP API 端点。

端点：
- POST /groups/{group_id}/fuse - 发起融合操作

LLM 增强：
- 设置环境变量 LLM_ENABLED=true 启用 LLM recommendation
- 需要同时配置 LLM_BASE_URL 和 LLM_AUTH_TOKEN

LLM 认证说明（重要）：
- 部分 LLM 端点需要同时支持 x-api-key 和 Authorization: Bearer
- 代码已自动处理（anthropic_compatible_provider.py:_build_headers）
- 不要删除任何认证 header，否则会导致 401 错误

Stage 1 Phase 4.5:
- 使用 fusion_dependencies 进行依赖注入
- Registry-aware filtering 默认启用
- G5 模式会过滤 offline worker
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Path, Request, status

from src.infra.context import set_current_cookie
from pydantic import BaseModel, Field

from src.domain.models.fusion_request import FusionRequest
from src.domain.models.fusion_result import FusionResult
from src.application.services.group_fusion_service import GroupFusionService
from src.domain.services.perspective_provider import PerspectiveProvider


# =============================================================================
# Response Models (Pydantic models for OpenAPI schema generation)
# =============================================================================

class PerspectiveResponse(BaseModel):
    """视角响应模型"""
    model_config = {"extra": "forbid"}

    participant_id: str
    participant_type: Literal["bot", "human", "system"]
    role: Literal["driver", "consultant", "observer", "expert"]
    summary: str
    confidence: Optional[float] = None
    evidence: list[str] = Field(default_factory=list)
    status: Literal["completed", "timed_out", "failed", "skipped"]
    # G2 扩展字段
    key_points: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    flexibility: Optional[str] = None


class RecommendationResponse(BaseModel):
    """建议响应模型"""
    model_config = {"extra": "forbid"}

    summary: str
    decision: Literal["yes", "no", "conditional_yes", "needs_more_information"]
    risks: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class TimingResponse(BaseModel):
    """计时响应模型"""
    model_config = {"extra": "forbid"}

    started_at: datetime
    finished_at: datetime
    duration_ms: int


class ConflictResponse(BaseModel):
    """冲突响应模型（G2）"""
    model_config = {"extra": "forbid"}

    parties: list[str]
    issue: str
    positions: list[str]
    severity: Literal["low", "medium", "high", "critical"]


class AlignmentPointResponse(BaseModel):
    """对齐点响应模型（G2）"""
    model_config = {"extra": "forbid"}

    summary: str
    participants: Optional[list[str]] = None


# =============================================================================
# G5: Expert Diagnosis Response Models
# =============================================================================

class RiskAssessmentResponse(BaseModel):
    """风险评估响应模型（G5）"""
    model_config = {"extra": "forbid"}

    overall: Literal["low", "medium", "high", "critical"]
    categories: dict[str, Literal["low", "medium", "high", "critical"]] = Field(default_factory=dict)


class CriticalIssueResponse(BaseModel):
    """关键问题响应模型（G5）"""
    model_config = {"extra": "forbid"}

    issue: str
    severity: Literal["low", "medium", "high", "critical"]
    domain: str
    source: str
    description: Optional[str] = None


class ExpertRecommendationResponse(BaseModel):
    """专家建议响应模型（G5）"""
    model_config = {"extra": "forbid"}

    priority: Literal["P0", "P1", "P2"]
    action: str
    owner: Optional[str] = None
    domain: Optional[str] = None
    deadline: Optional[datetime] = None


class FuseResponse(BaseModel):
    """融合响应模型"""
    model_config = {"extra": "forbid"}

    group_id: str
    fusion_id: str
    question: str
    driver_bot_id: Optional[str] = None
    perspectives: list[PerspectiveResponse]
    recommendation: Optional[RecommendationResponse] = None
    partial_success: bool
    warnings: list[str]
    errors: list[str]
    timing: TimingResponse
    # G2 扩展字段
    fusion_mode: Literal["agent", "conflict_alignment", "expert_diagnosis", "bot_profile_fuse"] = "agent"
    conflicts: list[ConflictResponse] = Field(default_factory=list)
    alignment_points: list[AlignmentPointResponse] = Field(default_factory=list)
    key_insights: list[str] = Field(default_factory=list)
    conclusion: Optional[Any] = Field(
        default=None,
        description="冲突结论（G2），类型为 ConflictConclusion",
    )
    # G5 扩展字段
    risk_assessment: Optional[RiskAssessmentResponse] = None
    critical_issues: list[CriticalIssueResponse] = Field(default_factory=list)
    recommendations: list[ExpertRecommendationResponse] = Field(default_factory=list)
    go_live_conditions: list[str] = Field(default_factory=list)
    summary: Optional[str] = None
    # V2 扩展字段
    structured_risk: Optional[Any] = Field(
        default=None,
        description="结构化风险评估（G5 V2），类型为 StructuredRiskAssessment",
    )
    structured_conflict_analysis: Optional[Any] = Field(
        default=None,
        description="结构化冲突分析（G2 V2），类型为 StructuredConflictAnalysis",
    )
    # G2 分析来源 - 三层Fallback架构来源标识
    analysis_source: Optional[str] = Field(
        default=None,
        description="G2分析来源（llm/v2/legacy），标识三层Fallback使用的是哪一层",
    )
    # 扩展结果字段 - 存放各模式的扩展处理信息
    extend_result: Optional[dict[str, Any]] = Field(
        default=None,
        description="扩展结果，G9模式包含fused_profile和group_conversation",
    )


class ErrorObjectResponse(BaseModel):
    """错误对象响应模型"""
    model_config = {"extra": "forbid"}

    code: str
    message: str
    details: list[str] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    """错误响应模型"""
    model_config = {"extra": "forbid"}

    error: ErrorObjectResponse


# =============================================================================
# Service Setup
# =============================================================================

# Stage 1 Phase 4.5: Use fusion_dependencies for proper DI
# This ensures Registry-aware filtering is enabled for G5 mode
from src.interfaces.api.dependencies.fusion_dependencies import (
    get_group_fusion_service as _get_group_fusion_service,
    reset_fusion_services,
    configure_filter,
)

# Legacy compatibility: Keep global provider for tests
_provider: PerspectiveProvider | None = None


def _create_llm_recommendation_service():
    """
    创建 LLM Recommendation Service（如果环境配置正确）

    Returns:
        FusionRecommendationService 或 None

    Environment Variables:
        ENABLE_REAL_LLM (preferred): Canonical flag to enable real LLM
        LLM_ENABLED (legacy): Legacy flag for backward compatibility
    """
    # 检查是否启用 LLM - 支持 canonical flag 和 legacy flag
    canonical_enabled = os.environ.get("ENABLE_REAL_LLM", "").lower() == "true"
    legacy_enabled = os.environ.get("LLM_ENABLED", "").lower() == "true"
    llm_enabled = canonical_enabled or legacy_enabled

    # 记录 flag 状态（用于诊断）
    if llm_enabled:
        flag_source = "ENABLE_REAL_LLM" if canonical_enabled else "LLM_ENABLED"
        logger.info(f"[Fusion] LLM enabled via {flag_source}")
    else:
        logger.info("[Fusion] LLM not enabled (both ENABLE_REAL_LLM and LLM_ENABLED are not 'true')")

    if not llm_enabled:
        return None

    # 检查必要的环境变量
    base_url = os.environ.get("LLM_BASE_URL")
    auth_token = os.environ.get("LLM_AUTH_TOKEN")

    if not base_url or not auth_token:
        logger.warning(
            f"[Fusion] LLM enabled but configuration incomplete: "
            f"base_url={'set' if base_url else 'MISSING'}, "
            f"auth_token={'set' if auth_token else 'MISSING'}"
        )
        return None

    # 创建 LLM 服务链
    try:
        from src.infra.llm.config.llm_settings import LLMSettings
        from src.infra.llm.providers.anthropic_compatible_provider import AnthropicCompatibleProvider
        from src.infra.llm.routing.static_llm_router import StaticLLMRouter
        from src.application.services.llm_gateway_service import LLMGatewayService
        from src.application.services.fusion_recommendation_service import FusionRecommendationService

        settings = LLMSettings()
        provider = AnthropicCompatibleProvider(settings=settings)
        router = StaticLLMRouter(settings=settings)
        gateway = LLMGatewayService(provider=provider, router=router)

        logger.info(
            f"[Fusion] LLM recommendation service created successfully "
            f"(model={settings.model}, base_url={base_url})"
        )

        return FusionRecommendationService(gateway=gateway)
    except Exception as e:
        # LLM 服务创建失败，记录错误并回退到规则方法
        logger.error(
            f"[Fusion] Failed to create LLM recommendation service: {e}. "
            f"Falling back to deterministic method."
        )
        return None


def get_service() -> GroupFusionService:
    """
    获取服务实例

    Stage 1 Phase 4.5:
    - 使用 fusion_dependencies 获取服务
    - 确保Registry-aware filtering 已启用

    测试支持:
    - 如果设置了测试 provider (_provider)，创建使用该 provider 的服务
    """
    global _provider
    if _provider is not None:
        # 测试模式：使用测试设置的 provider 创建简单服务
        # 每次都创建新实例，避免单例缓存导致状态污染
        from src.application.services.participant_availability_checker import ParticipantAvailabilityChecker
        from src.infra.adapters.sqlite_worker_profile_binding_store import SQLiteWorkerProfileBindingStore
        from src.infra.adapters.sqlite_worker_runtime_state_store import SQLiteWorkerRuntimeStateStore

        # 创建简单的 availability checker
        profile_binding_store = SQLiteWorkerProfileBindingStore(":memory:")
        runtime_state_store = SQLiteWorkerRuntimeStateStore(":memory:")
        availability_checker = ParticipantAvailabilityChecker(
            profile_binding_store=profile_binding_store,
            runtime_state_store=runtime_state_store,
        )

        return GroupFusionService(
            provider=_provider,
            availability_checker=availability_checker,
            worker_store=None,  # 测试路径不注入，跳过 fusion_enable 检查
        )

    return _get_group_fusion_service()


def set_provider(provider: PerspectiveProvider) -> None:
    """设置 provider（用于测试）"""
    global _provider
    _provider = provider
    # Reset and reconfigure
    reset_fusion_services()


# =============================================================================
# Router
# =============================================================================

router = APIRouter()


@router.post(
    "/groups/{group_id}/fuse",
    response_model=FuseResponse,
    responses={
        200: {"description": "Fusion completed successfully or partially successfully"},
        400: {"description": "Invalid request", "model": ErrorResponse},
        404: {"description": "Group or participant not found", "model": ErrorResponse},
        422: {"description": "Validation failed", "model": ErrorResponse},
        500: {"description": "Internal error", "model": ErrorResponse},
    },
)
def create_group_fusion(
    http_request: Request,
    group_id: str = Path(
        ...,
        description="Existing group/session identifier"
    ),
    request: FusionRequest = None,  # type: ignore
) -> FuseResponse:
    """
    发起融合操作

    在指定 group 上对一个明确问题发起多参与者视角融合。

    Args:
        group_id: Group 标识符
        request: 融合请求

    Returns:
        FuseResponse: 融合结果
    """
    # 提取 Cookie 并设置到上下文，用于 BCN API 调用认证
    cookie = http_request.headers.get("cookie", "")
    logger.info(f"[FUSION-API] cookie={'yes' if cookie else 'no'}({len(cookie) if cookie else 0})")

    # 设置到上下文，供后续 BCN API 调用使用
    set_current_cookie(cookie)

    try:
        service = get_service()
        result = service.fuse(request, group_id=group_id)

        # 日志：记录FusionResult关键信息
        logger.info(f"[FUSION-API] ========== FusionResult 收到 ==========")
        logger.info(f"[FUSION-API] fusion_id={result.fusion_id}, fusion_mode={result.fusion_mode}")
        logger.info(f"[FUSION-API] perspectives={len(result.perspectives)}, conflicts={len(result.conflicts)}, alignment_points={len(result.alignment_points)}")
        logger.info(f"[FUSION-API] conclusion字段: {'存在' if result.conclusion else '不存在'}")
        if result.conclusion:
            logger.info(f"[FUSION-API] conclusion类型: {type(result.conclusion).__name__}")
            if hasattr(result.conclusion, 'overall_severity'):
                logger.info(f"[FUSION-API] conclusion.overall_severity={result.conclusion.overall_severity}")
            if hasattr(result.conclusion, 'go_no_go'):
                logger.info(f"[FUSION-API] conclusion.go_no_go={result.conclusion.go_no_go}")

        # 转换为响应模型
        logger.info("[FUSION-API] 开始构建FuseResponse...")

        # 构建conclusion响应
        conclusion_data = None
        if result.conclusion:
            if hasattr(result.conclusion, 'model_dump'):
                conclusion_data = result.conclusion.model_dump()
                logger.info(f"[FUSION-API] conclusion已序列化: {list(conclusion_data.keys()) if conclusion_data else 'None'}")
            else:
                conclusion_data = result.conclusion
                logger.info(f"[FUSION-API] conclusion直接使用(非Pydantic): {type(conclusion_data)}")

        response = FuseResponse(
            group_id=result.group_id,
            fusion_id=result.fusion_id,
            question=result.question,
            driver_bot_id=result.driver_bot_id,
            perspectives=[
                PerspectiveResponse(**p.model_dump()) for p in result.perspectives
            ],
            recommendation=(
                RecommendationResponse(**result.recommendation.model_dump())
                if result.recommendation else None
            ),
            partial_success=result.partial_success,
            warnings=result.warnings,
            errors=result.errors,
            timing=TimingResponse(**result.timing.model_dump()),
            # G2 字段
            fusion_mode=result.fusion_mode,
            conflicts=[
                ConflictResponse(**c.model_dump() if hasattr(c, 'model_dump') else c)
                for c in result.conflicts
            ],
            alignment_points=[
                AlignmentPointResponse(**a.model_dump() if hasattr(a, 'model_dump') else a)
                for a in result.alignment_points
            ],
            key_insights=result.key_insights,
            conclusion=conclusion_data,
            # G5 字段
            risk_assessment=(
                RiskAssessmentResponse(**result.risk_assessment.model_dump())
                if result.risk_assessment else None
            ),
            critical_issues=[
                CriticalIssueResponse(**i.model_dump() if hasattr(i, 'model_dump') else i)
                for i in result.critical_issues
            ],
            recommendations=[
                ExpertRecommendationResponse(**r.model_dump() if hasattr(r, 'model_dump') else r)
                for r in result.recommendations
            ],
            go_live_conditions=result.go_live_conditions,
            summary=result.summary,
            # V2 字段
            structured_risk=result.structured_risk.model_dump() if result.structured_risk else None,
            structured_conflict_analysis=result.structured_conflict_analysis.model_dump() if result.structured_conflict_analysis else None,
            # G2 分析来源
            analysis_source=result.analysis_source,
            # 扩展结果字段 - 直接使用 result 中的 extend_result
            extend_result=result.extend_result,
        )

        logger.info(f"[FUSION-API] ✅ FuseResponse构建完成: conclusion={'有' if response.conclusion else '无'}")
        logger.info(f"[FUSION-API] ========== API响应准备返回 ==========")

        return response

    except Exception as e:
        import traceback
        traceback.print_exc()  # 打印完整堆栈
        logger.error(f"[Fusion] Error processing request: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(e),
                },
            },
        )


__all__ = [
    "router",
    "FuseResponse",
    "PerspectiveResponse",
    "RecommendationResponse",
    "TimingResponse",
    "ConflictResponse",
    "AlignmentPointResponse",
    "RiskAssessmentResponse",
    "CriticalIssueResponse",
    "ExpertRecommendationResponse",
    "ErrorResponse",
    "set_provider",
    "reset_fusion_services",
    "configure_filter",
]