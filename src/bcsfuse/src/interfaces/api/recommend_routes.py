"""
Bot Recommendation API Routes

Bot 推荐接口,根据问题自动推荐最合适的 Bot 列表。

端点:
- POST /recommend - 推荐 Bot 列表

与 G1/G2/G5 的关系:
- 本接口专注于"智能推荐",不在本接口做融合决策
- 返回推荐的 Bot 列表
- 用户可基于推荐结果调用 G1/G2/G5 进行融合决策

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

from fastapi import APIRouter, status

from src.domain.models.bot_recommendation import (
    BotRecommendationRequest,
    BotRecommendationResponse,
    create_bot_recommendation_response,
)
from src.domain.models.retrieval_mode import RetrievalMode
from src.interfaces.api.dependencies.fusion_dependencies import (
    get_candidate_recommendation_service,
    get_question_rewrite_service,
)
from src.infra.trace_context import get_trace_id
from src.application.utils.drm_config_helper import get_recommend_min_score

logger = logging.getLogger(__name__)

router = APIRouter()


def _enrich_trust_level(response: BotRecommendationResponse) -> None:
    """从 Worker Registry 补充推荐结果中的 trust_level 到 profile_tags。"""
    try:
        from src.interfaces.api.dependencies.worker_dependencies import get_registry_store
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
- `reranker_model`: 默认使用环境变量 `RERANKER_MODEL` 或 `bge-reranker-v2-m3`

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
async def recommend_bots(request: BotRecommendationRequest) -> BotRecommendationResponse:
    """
    推荐 Bot

    当提供 group_id 时,会先获取群组上下文并改写问题,再用改写后的问题进行推荐。

    Args:
        request: BotRecommendationRequest

    Returns:
        BotRecommendationResponse: 包含推荐的 Bot 列表
    """
    trace_id = get_trace_id()  # 从中间件设置的 contextvars 读取，用于传递给响应

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

        # 优先级: request.rerank_min_score > explicit request.min_score > BCSFUSE_RERANK_MIN_SCORE > legacy BCSFUSE_RECOMMEND_MIN_SCORE > default 0.6
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
            rerank_min_score = 0.6
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

    logger.info(
        f"[RECOMMEND][{trace_id}] request | "
        f"question={request.question}, topK={request.topK}, type={request.type}, "
        f"min_score={request.min_score}, group_id={request.group_id}, "
        f"expand_factor={request.expand_factor}, enable_rerank={enable_rerank}, "
        f"vector_min_score={vector_min_score}, rerank_min_score={rerank_min_score}"
    )

    # 兼容性诊断日志
    logger.info(
        f"[RECOMMEND][{trace_id}] COMPAT | "
        f"explicit_min_score_provided={explicit_min_score_provided}, "
        f"min_score_source={min_score_source}, "
        f"compat_mode={min_score_compat_mode}, "
        f"stage={min_score_stage}, "
        f"drm_legacy_min_score={drm_legacy_min_score}, "
        f"drm_vector_min_score={drm_vector_min_score}, "
        f"drm_rerank_min_score={drm_rerank_min_score}"
    )

    # 确定用于推荐的问题
    rewrite_question = request.question

    if request.group_id:
        rewrite_question = await _rewrite_with_group_context(
            question=request.question,
            group_id=request.group_id,
            trace_id=trace_id,
        )

    # 获取服务
    candidate_service = get_candidate_recommendation_service()

    # DIAGNOSTIC: 检查服务状态和配置
    from src.infra.config.feature_flags import FeatureFlags
    vector_aware_enabled = FeatureFlags.is_vector_aware_recommendation_enabled()
    real_embedding_enabled = FeatureFlags.is_real_embedding_enabled()

    # 检查服务注入状态
    has_vector_match = candidate_service._vector_match_service is not None
    has_embedding_gen = candidate_service._embedding_generator is not None

    logger.info(
        f"[RECOMMEND][{trace_id}] DIAGNOSTIC | "
        f"route=recommend_routes.py (REAL VECTOR ROUTE), "
        f"vector_aware_flag={vector_aware_enabled}, "
        f"real_embedding_flag={real_embedding_enabled}, "
        f"has_vector_match_service={has_vector_match}, "
        f"has_embedding_generator={has_embedding_gen}, "
        f"retrieval_mode=EXPERT_DIAGNOSIS"
    )

    # 构建向量搜索运行时配置
    runtime_config = {}
    runtime_config["expand_factor"] = request.expand_factor

    # 处理 Reranker 配置（默认开启）
    default_reranker_model = os.getenv("RERANKER_MODEL", "bge-reranker-v2-m3")

    if enable_rerank:
        runtime_config["reranker_model"] = request.reranker_model or default_reranker_model
    else:
        runtime_config["reranker_model"] = None

    # 设置默认过滤条件：如果未传 filters，默认只搜索 protected 和 public 的 profile
    filters = request.filters
    if filters is None:
        filters = {"availability": ["protected", "public"]}

    logger.info(
        f"[RECOMMEND][{trace_id}] config | "
        f"enable_rerank_requested={enable_rerank}, "
        f"reranker_model={runtime_config.get('reranker_model')}, "
        f"expand_factor={runtime_config.get('expand_factor')}, "
        f"filters={filters}"
    )

    # 核心:复用现有服务,participants=None 触发全库推荐
    # Phase B: 传递两个独立的阈值
    logger.info(
        f"[RECOMMEND][{trace_id}] CALLING SERVICE | "
        f"service=WorkerCandidateRecommendationImpl.recommend, "
        f"mode=EXPERT_DIAGNOSIS, "
        f"participants=None (全库推荐), "
        f"max_candidates={request.topK}, "
        f"vector_min_score={vector_min_score}, "
        f"rerank_min_score={rerank_min_score}"
    )

    candidate_response = candidate_service.recommend(
        question=rewrite_question,
        mode=RetrievalMode.EXPERT_DIAGNOSIS,  # 使用专家诊断模式进行检索
        participants=None,  # 关键:不传 participants,触发全库推荐
        max_candidates=request.topK,
        runtime_config=runtime_config if runtime_config else None,
        filters=filters,
        vector_min_score=vector_min_score,  # Phase B: 向量召回阈值
        rerank_min_score=rerank_min_score,  # Phase B: rerank 后质量阈值
    )

    # DIAGNOSTIC: 记录响应详情
    logger.info(
        f"[RECOMMEND][{trace_id}] SERVICE RESPONSE | "
        f"recommendations_count={len(candidate_response.recommendations)}, "
        f"total_candidates={candidate_response.total_candidates}, "
        f"selected_candidates={candidate_response.selected_candidates}, "
        f"mode={candidate_response.mode}, "
        f"retrieval_source={getattr(candidate_response, 'retrieval_source', 'unknown')}, "
        f"metadata={getattr(candidate_response, 'metadata', {})}"
    )

    # Phase D: 构建响应 metadata（在创建响应之前）
    metadata = candidate_response.metadata
    metadata.update({
        # Phase B: Threshold 信息
        "vector_min_score": vector_min_score,
        "rerank_min_score": rerank_min_score,
        "enable_rerank": enable_rerank,
        "reranker_model": runtime_config.get("reranker_model") if enable_rerank else None,
        "expand_factor": runtime_config.get("expand_factor", 2),

        # Phase F: Backward Compatibility 信息
        "explicit_min_score_provided": explicit_min_score_provided,
        "min_score_source": min_score_source,
        "min_score_compat_mode": min_score_compat_mode,
        "min_score_stage": min_score_stage,

        # 向量搜索信息
        "candidate_source": metadata.get("candidate_source", "vector"),
        "vector_search_used": True,

        # Fragment 和 Rerank 信息
        "fragment_embedding_enabled": FeatureFlags.is_profile_embedding_index_enabled(),
        "content_reload_enabled": True,  # Phase C: 已启用 content reload

        # 统计信息（需要在创建 response 之后更新）
        "recommendations_count": 0,  # 临时占位，稍后更新
        "total_candidates": candidate_response.total_candidates,
        "selected_candidates": candidate_response.selected_candidates,
    })

    # 转换为 BotRecommendationResponse
    response = create_bot_recommendation_response(
        candidate_response=candidate_response,
        driver_bot_id=request.driver_bot_id,
        trace_id=trace_id,
        query_type=request.type,
    )

    # 更新 recommendations_count
    response.metadata["recommendations_count"] = len(response.recommendations)

    # 从 Worker Registry 补充 trust_level
    _enrich_trust_level(response)

    # Phase D: 输出诊断 metadata
    logger.info(
        f"[RECOMMEND][{trace_id}] DIAGNOSTIC_METADATA | "
        f"candidate_source={metadata.get('candidate_source', 'unknown')}, "
        f"vector_search_used={metadata.get('vector_search_used', False)}, "
        f"fragment_embedding_enabled={metadata.get('fragment_embedding_enabled', False)}, "
        f"content_reload_enabled={metadata.get('content_reload_enabled', False)}, "
        f"content_reload_source={metadata.get('content_reload_source', 'none')}, "
        f"vector_min_score={metadata.get('vector_min_score', 'N/A')}, "
        f"rerank_min_score={metadata.get('rerank_min_score', 'N/A')}, "
        f"enable_rerank={metadata.get('enable_rerank', False)}, "
        f"reranker_model={metadata.get('reranker_model', 'none')}, "
        f"expand_factor={metadata.get('expand_factor', 2)}, "
        f"explicit_min_score_provided={metadata.get('explicit_min_score_provided', False)}, "
        f"min_score_source={metadata.get('min_score_source', 'unknown')}, "
        f"min_score_compat_mode={metadata.get('min_score_compat_mode', 'unknown')}, "
        f"min_score_stage={metadata.get('min_score_stage', 'unknown')}"
    )

    # DIAGNOSTIC: 记录最终响应摘要
    top_rec = response.recommendations[0] if response.recommendations else None
    logger.info(
        f"[RECOMMEND][{trace_id}] FINAL RESPONSE | "
        f"recommendations_count={len(response.recommendations)}, "
        f"driver_bot_id={response.driver_bot_id}, "
        f"top_score={top_rec.score if top_rec else 'N/A'}, "
        f"top_profile_key={top_rec.profile_key if top_rec else 'N/A'}, "
        f"top_reasons={top_rec.reasons if top_rec else 'N/A'}"
    )

    # 详细日志（仅在 debug 模式）
    logger.debug(
        f"[RECOMMEND][{trace_id}] FULL RESPONSE | %s",
        response.model_dump()
    )

    return response


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
    rewrite_service = get_question_rewrite_service()

    if rewrite_service is None:
        logger.info(
            f"[RECOMMEND][{trace_id}] QuestionRewriteService 不可用, 使用原始问题"
        )
        return question

    try:
        result = await rewrite_service.rewrite(question=question, group_id=group_id)

        if result.context_messages_count == 0:
            logger.info(
                f"[RECOMMEND][{trace_id}] 群组 {group_id} 无上下文消息, 使用原始问题"
            )
            return question

        logger.info(
            f"[RECOMMEND][{trace_id}] 问题已改写: "
            f"original='{question}' -> rewritten='{result.rewritten_question}'"
        )
        return result.rewritten_question

    except Exception as e:
        logger.warning(
            f"[RECOMMEND][{trace_id}] 问题改写失败, 使用原始问题: {e}"
        )
        return question


__all__ = ["router"]