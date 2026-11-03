"""
Bot Recommendation API Routes

Bot 推荐接口,根据问题自动推荐最合适的 Bot 列表。

端点:
- POST /recommend - 推荐 Bot 列表

与 G1/G2/G5 的关系:
- 本接口专注于"智能推荐",不在本接口做融合决策
- 返回推荐的 Bot 列表
- 用户可基于推荐结果调用 G1/G2/G5 进行融合决策

当前搜索边界（2026-10-09）：
- 本接口固定使用 EXPERT_DIAGNOSIS 场景标记，不提供四选一的搜索模式。
- 正常主链路由 WorkerVectorMatchService 执行片段向量/关键词召回、
  RRF 合并及可选精排；标记不意味着使用旧 ModeAwareScorer 决定主链路排序。
- 已移除的旧 AGENT 混合检索实验不是本接口的搜索实现；融合业务模式
  与搜索算法分开维护，本接口历史兼容/降级行为不因该清理而改变。

群组上下文增强:
- 当提供 group_id 时,系统会获取群组最近消息并用 LLM 改写问题
- 改写后的问题更具上下文完整性,能提升推荐准确性

向量搜索高级参数:
- expand_factor: 扩大召回倍数
- enable_rerank: 是否启用 Reranker
- reranker_model: Reranker 模型名
"""

from __future__ import annotations

import logging
import os
from time import perf_counter
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.adapters.http.bot_recommendation_projection import (
    build_v1_recommendation_response,
)
from app.domain.models.bot_recommendation import (
    BotRecommendation,
    BotRecommendationRequest,
    BotRecommendationResponse,
)
from app.adapters.http.dependencies.fusion_dependencies import (
    get_candidate_recommendation_service,
    get_question_rewrite_service,
)
from app.adapters.http.search_threadpool import run_search_in_threadpool
from app.application.agent_search_service import (
    AgentRecommendationCommand,
    AgentSearchService,
)
from app.infra.trace_context import get_trace_id
from app.application.utils.drm_config_helper import get_recommend_min_score
from app.infra.config.feature_flags import FeatureFlags

from app.domain.services.retrieval_logging import log_stage, log_candidates

logger = logging.getLogger(__name__)

router = APIRouter()


def _final_recommendation_projection(
    command: AgentRecommendationCommand,
    request: BotRecommendationRequest,
    trace_id: str,
    *,
    error_type: str,
    local_snapshot: Any | None = None,
) -> BotRecommendationResponse:
    """Return a non-empty last-resort envelope when projection fails."""

    outcome = AgentSearchService.final_recommendation_outcome(
        command,
        error_type=error_type,
        local_snapshot=local_snapshot,
    )
    recommendations = [
        BotRecommendation(
            profile_key=rec.profile_key,
            worker_id=rec.worker_id,
            score=rec.score,
            reasons=rec.reasons,
            short_profile=rec.short_profile,
            profile_tags={"trust_level": rec.trust_level} if rec.trust_level else {},
        )
        for rec in outcome.response.recommendations
    ]
    driver_bot_id = request.driver_bot_id
    if driver_bot_id is None and recommendations:
        driver_bot_id = recommendations[0].profile_key
    return BotRecommendationResponse(
        trace_id=trace_id,
        type=request.type,
        driver_bot_id=driver_bot_id,
        recommendations=recommendations,
        metadata=outcome.response.metadata,
    )


def _enrich_trust_level(response: BotRecommendationResponse) -> None:
    """从 Worker Registry 补充推荐结果中的 trust_level 到 profile_tags。"""
    try:
        from app.adapters.http.dependencies.worker_dependencies import get_registry_store
        store = get_registry_store()
        if store is None:
            return
        for rec in response.recommendations:
            if "trust_level" in rec.profile_tags:
                continue
            worker = store.get_by_id(rec.worker_id)
            if worker and worker.state and worker.state.trust_level:
                rec.profile_tags = {**rec.profile_tags, "trust_level": worker.state.trust_level.value}
    except Exception:
        logger.debug("Failed to enrich trust_level for recommendations", exc_info=True)


@router.post(
    "/recommend",
    response_model=BotRecommendationResponse,
    status_code=status.HTTP_200_OK,
    summary="推荐合适的 Bot",
    description="""
根据问题自动推荐最合适的 Bot 列表。

**核心功能**:
- 基于问题和现有 Worker Profile 自动匹配最合适的 Bot
- 推断问题所需的领域,确保领域覆盖完整性
- 支持通过群组上下文改写问题,提升推荐准确性

**群组上下文增强**:
- 提供 `group_id` 时,系统会获取群组最近 10 条消息
- 结合上下文用 LLM 改写问题(如替换代词、补充省略信息)
- 改写后的问题更完整,推荐结果更精准

**与 G1/G2/G5 的协作**:
1. 调用本接口获取推荐的 Bot 列表
2. 使用 `recommendations[].profile_key` 作为 Fusion 接口的 `participants` 参数

**默认行为**:
- `enable_rerank`: 默认为 `true`（开启精排）
- `expand_factor`: 默认为 `3`
- `reranker_model`: 默认使用环境变量 `RERANKER_MODEL` 或 `Qwen3-Reranker-4B`

**示例请求**:
```json
{
    "question": "电商大促活动技术方案的风险评估",
    "topK": 5,
    "min_score": 0.01,
    "driver_bot": "bot_abc123",
    "group_id": "grp_abc123"
}
```

**关闭精排的示例**:
```json
{
    "question": "简单的问候",
    "topK": 5,
    "enable_rerank": false
}
```
""",
)
async def recommend_bots(
    http_request: Request,
    request: BotRecommendationRequest,
) -> BotRecommendationResponse:
    """
    推荐 Bot

    当提供 group_id 时,会先获取群组上下文并改写问题,再用改写后的问题进行推荐。

    Args:
        request: BotRecommendationRequest

    Returns:
        BotRecommendationResponse: 包含推荐的 Bot 列表
    """
    from app.bootstrap.oss_business_routes import require_oss_auth

    require_oss_auth(http_request)
    trace_id = get_trace_id()  # 从中间件设置的 contextvars 读取，用于传递给响应
    started = perf_counter()

    # ========================================================================
    # Phase F: min_score Backward Compatibility
    # ========================================================================
    # 检测 request.min_score 是否显式传入（区分显式传入 vs 默认值）
    # Pydantic v2 使用 model_fields_set
    explicit_min_score_provided = 'min_score' in request.model_fields_set

    # 获取 DRM 环境变量配置
    drm_legacy_min_score = get_recommend_min_score()  # BCSFUSE_RECOMMEND_MIN_SCORE (legacy)
    drm_vector_min_score_str = os.getenv("BCSFUSE_VECTOR_MIN_SCORE")  # 新：向量召回阈值
    drm_rerank_min_score_str = os.getenv("BCSFUSE_RERANK_MIN_SCORE")  # 新：rerank后阈值

    # 解析新环境变量
    drm_vector_min_score = float(drm_vector_min_score_str) if drm_vector_min_score_str else None
    drm_rerank_min_score = float(drm_rerank_min_score_str) if drm_rerank_min_score_str else None

    # 确定是否启用 rerank
    enable_rerank = request.enable_rerank if request.enable_rerank is not None else True

    # ========================================================================
    # 阈值计算逻辑（兼容性优先级）
    # ========================================================================
    if enable_rerank:
        # === enable_rerank=true 时 ===
        # - vector_min_score: 仅用于向量召回，确保召回足够候选
        # - rerank_min_score: 用于 rerank 后质量过滤

        # 优先级: request.vector_min_score > BCSFUSE_VECTOR_MIN_SCORE > default 0.01
        # 注意：request 暂无 vector_min_score 字段，未来可扩展
        vector_min_score = drm_vector_min_score if drm_vector_min_score is not None else 0.01

        # 优先级: request.rerank_min_score > explicit request.min_score > BCSFUSE_RERANK_MIN_SCORE > legacy BCSFUSE_RECOMMEND_MIN_SCORE > default 0.01
        if explicit_min_score_provided:
            # 关键兼容性：显式传入的 min_score 用作 rerank_min_score，不影响向量召回
            rerank_min_score = request.min_score
            min_score_source = "explicit_request"
        elif drm_rerank_min_score is not None:
            rerank_min_score = drm_rerank_min_score
            min_score_source = "BCSFUSE_RERANK_MIN_SCORE"
        elif drm_legacy_min_score is not None:
            # legacy DRM 配置：用于 rerank 阈值，而非向量召回阈值
            rerank_min_score = drm_legacy_min_score
            min_score_source = "legacy_BCSFUSE_RECOMMEND_MIN_SCORE"
        else:
            rerank_min_score = 0.01
            min_score_source = "default"

        min_score_compat_mode = "rerank_split"
        min_score_stage = "rerank_post_filter"

    else:
        # === enable_rerank=false 时 ===
        # 保持老逻辑：min_score 用于最终推荐过滤

        if explicit_min_score_provided:
            vector_min_score = request.min_score
            rerank_min_score = request.min_score
            min_score_source = "explicit_request"
        elif drm_legacy_min_score is not None:
            vector_min_score = drm_legacy_min_score
            rerank_min_score = drm_legacy_min_score
            min_score_source = "legacy_BCSFUSE_RECOMMEND_MIN_SCORE"
        else:
            vector_min_score = request.min_score  # 使用 schema 默认值 0.01
            rerank_min_score = request.min_score
            min_score_source = "schema_default"

        min_score_compat_mode = "legacy_unified"
        min_score_stage = "final_filter"

    effective_min_score = vector_min_score

    log_stage(logger, "request", question_length=len(request.question), top_k=request.topK,
              rerank_requested=enable_rerank, expand_factor=request.expand_factor)

    # 兼容性诊断日志
    logger.debug(
        f"[RECOMMEND][{trace_id}] COMPAT | "
        f"explicit_min_score_provided={explicit_min_score_provided}, "
        f"min_score_source={min_score_source}, "
        f"compat_mode={min_score_compat_mode}, "
        f"stage={min_score_stage}, "
        f"drm_legacy_min_score={drm_legacy_min_score}, "
        f"env_vector_min_score={drm_vector_min_score}, "
        f"env_rerank_min_score={drm_rerank_min_score}"
    )

    # 确定用于推荐的问题
    rewrite_question = request.question

    if request.group_id:
        rewrite_question = await _rewrite_with_group_context(
            question=request.question,
            group_id=request.group_id,
            trace_id=trace_id,
        )

    # 构建并执行统一应用服务命令。推荐仍是 query-based 检索，只是 projection
    # 和阈值业务语义与 search 不同；公共 normalization/default filters/
    # bounded execution 均由 AgentSearchService 承担。
    default_reranker_model = os.getenv("RERANKER_MODEL", "Qwen3-Reranker-4B")
    request_reranker_model = (
        request.reranker_model or default_reranker_model if enable_rerank else None
    )
    command = AgentRecommendationCommand(
        question=rewrite_question,
        top_k=request.topK,
        enable_rerank=enable_rerank,
        reranker_model=request_reranker_model,
        expand_factor=request.expand_factor,
        vector_min_score=vector_min_score,
        rerank_min_score=rerank_min_score,
        min_score_explicit=explicit_min_score_provided,
        filters=request.filters,
    )
    app_state = getattr(getattr(http_request, "app", None), "state", None)
    result_cache = getattr(app_state, "agent_search_result_cache", None)
    from app.application.services.search_index_state import (
        search_index_state_metadata,
    )
    context = getattr(app_state, "context", None)
    registry = getattr(context, "registry", None)
    try:
        # 获取服务
        candidate_service = get_candidate_recommendation_service()

        # DIAGNOSTIC: 检查服务状态和配置
        vector_aware_enabled = FeatureFlags.is_vector_aware_recommendation_enabled()
        real_embedding_enabled = FeatureFlags.is_real_embedding_enabled()

        # 检查服务注入状态；诊断信息不能让可用请求失败。
        has_vector_match = (
            getattr(candidate_service, "_vector_match_service", None) is not None
        )
        has_embedding_gen = (
            getattr(candidate_service, "_embedding_generator", None) is not None
        )

        logger.debug(
            f"[RECOMMEND][{trace_id}] DIAGNOSTIC | "
            f"route=recommend_routes.py (REAL VECTOR ROUTE), "
            f"vector_aware_flag={vector_aware_enabled}, "
            f"real_embedding_flag={real_embedding_enabled}, "
            f"has_vector_match_service={has_vector_match}, "
            f"has_embedding_generator={has_embedding_gen}, "
            f"retrieval_mode=EXPERT_DIAGNOSIS"
        )

        logger.debug(
            f"[RECOMMEND][{trace_id}] config | "
            f"enable_rerank_requested={enable_rerank}, "
            f"reranker_model={request_reranker_model}, "
            f"expand_factor={request.expand_factor}, "
            f"filter_fields={sorted(request.filters or {})}"
        )

        agent_search_service = AgentSearchService(
            candidate_recommendation_service=candidate_service,
            result_cache=result_cache,
            resilience_cache=getattr(
                app_state, "agent_search_resilience_cache", None
            ),
            local_snapshot=getattr(app_state, "local_retrieval_snapshot", None),
            execution_budget=getattr(
                app_state, "agent_search_execution_budget", None
            ),
            index_state_provider=(
                lambda: search_index_state_metadata(registry)
                if registry is not None
                else None
            ),
            retrieval_dependency_circuit=(
                getattr(app_state, "retrieval_dependency_circuit", None)
            ),
            offload=(
                getattr(
                    app_state, "retrieval_dependency_offload", None
                )
                or run_search_in_threadpool
            ),
        )
        outcome = await agent_search_service.recommend(command)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INVALID_RECOMMEND_COMMAND",
                "message": "recommendation request is invalid",
            },
        ) from error
    except Exception as error:
        logger.error("[RECOMMEND] core failed: %s", type(error).__name__)
        outcome = AgentSearchService.final_recommendation_outcome(
            command,
            error_type=type(error).__name__,
            local_snapshot=getattr(app_state, "local_retrieval_snapshot", None),
        )

    # Response projection, optional enrichment, cache publication, and logging are
    # also execution stages. An admitted recommendation must not become an
    # unexplained 500 if one of these late stages throws.
    try:
        candidate_response = outcome.response
        runtime_config = outcome.config_applied
        metadata = candidate_response.metadata
        direct_keyword_route = metadata.get("query_route") == "direct_keyword"
        direct_snapshot_used = metadata.get("direct_snapshot_used") is True
        metadata.update({
            "vector_min_score": vector_min_score,
            "rerank_min_score": rerank_min_score,
            "enable_rerank": enable_rerank,
            "reranker_model": (
                metadata.get("reranker_model", runtime_config.get("reranker_model"))
                if enable_rerank
                else None
            ),
            "expand_factor": runtime_config.get("expand_factor", 2),
            "explicit_min_score_provided": explicit_min_score_provided,
            "min_score_source": min_score_source,
            "min_score_compat_mode": min_score_compat_mode,
            "min_score_stage": min_score_stage,
            "candidate_source": metadata.get(
                "candidate_source",
                "direct_snapshot" if direct_snapshot_used else "vector",
            ),
            "vector_search_used": metadata.get(
                "vector_search_used",
                not direct_keyword_route
                and metadata.get("semantic_unavailable") is not True,
            ),
            "keyword_search_used": metadata.get(
                "keyword_search_used", False
            ) is True,
            "literal_fallback_used": metadata.get(
                "literal_fallback_used", False
            ) is True,
            "literal_fallback_default_used": metadata.get(
                "literal_fallback_default_used", False
            ) is True,
            "fragment_embedding_enabled": FeatureFlags.is_profile_embedding_index_enabled(),
            "content_reload_enabled": True,
            "recommendations_count": 0,
            "total_candidates": candidate_response.total_candidates,
            "selected_candidates": candidate_response.selected_candidates,
        })

        response = build_v1_recommendation_response(
            candidate_response,
            driver_bot_id=request.driver_bot_id,
            trace_id=trace_id,
            query_type=request.type,
        )
        response.metadata["recommendations_count"] = len(response.recommendations)

        # Registry reads may reach a synchronous durable store. Keep them off
        # the request event loop while retaining the bounded search worker budget.
        await run_search_in_threadpool(_enrich_trust_level, response)

        from app.adapters.http.recommend_response_cache import (
            store_v1_recommendation_response,
        )
        store_v1_recommendation_response(
            result_cache=result_cache,
            request=request,
            response=response,
        )

        top_rec = response.recommendations[0] if response.recommendations else None
        log_stage(
            logger,
            "response",
            recommendations_count=len(response.recommendations),
            duration_ms=round((perf_counter() - started) * 1000, 2),
            top_profile_key=top_rec.profile_key if top_rec else None,
            top_score=top_rec.score if top_rec else None,
        )
        log_candidates(
            logger,
            "response",
            ((r.profile_key, r.score) for r in response.recommendations),
        )

        return response
    except Exception as error:
        logger.error("[RECOMMEND] projection failed: %s", type(error).__name__)
        return _final_recommendation_projection(
            command,
            request,
            trace_id,
            error_type=type(error).__name__,
            local_snapshot=getattr(app_state, "local_retrieval_snapshot", None),
        )


async def _rewrite_with_group_context(question: str, group_id: str, trace_id: str = "") -> str:
    """
    使用群组上下文改写问题

    如果 QuestionRewriteService 不可用(如 LLM 未配置),则返回原问题。

    Args:
        question: 原始问题
        group_id: 群组 ID
        trace_id: 追踪 ID，用于日志关联

    Returns:
        改写后的问题,或原始问题(如果改写不可用)
    """
    try:
        rewrite_service = get_question_rewrite_service()

        if rewrite_service is None:
            logger.info(
                f"[RECOMMEND][{trace_id}] QuestionRewriteService 不可用, 使用原始问题"
            )
            return question

        result = await rewrite_service.rewrite(question=question, group_id=group_id)

        if result.context_messages_count == 0:
            logger.info(
                f"[RECOMMEND][{trace_id}] 群组 {group_id} 无上下文消息, 使用原始问题"
            )
            return question

        logger.info(
            f"[RECOMMEND][{trace_id}] 问题已改写: "
            f"original_length={len(question)}, rewritten_length={len(result.rewritten_question)}"
        )
        return result.rewritten_question

    except Exception as e:
        logger.warning(
            f"[RECOMMEND][{trace_id}] 问题改写失败, 使用原始问题: {type(e).__name__}"
        )
        return question


__all__ = ["router"]
