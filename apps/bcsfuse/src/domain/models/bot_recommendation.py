"""
BotRecommendation

Bot 推荐接口的请求和响应模型。

用于根据问题自动推荐合适的 Bot,为后续 G1/G2/G5 Fusion 提供候选人列表。
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field

from src.domain.models.candidate_recommendation import CandidateRecommendationResponse


class BotRecommendationRequest(BaseModel):
    """
    Bot 推荐请求

    根据问题自动推荐最合适的 Bot 列表。

    Attributes:
        question: 用户的问题/任务描述
        topK: 返回的推荐 bot 数量
        driver_bot_id: 主导 Bot 的 profile_key(可选)
        min_score: 推荐分数阈值(范围 0-1)
        expand_factor: 扩大召回倍数（向量搜索）
        enable_rerank: 是否启用 Reranker
        reranker_model: Reranker 模型名
        filters: 元数据过滤条件，默认只搜索 protected 和 public 的 profile
    """

    question: str = Field(
        min_length=1,
        description="用户的问题/任务描述,系统根据此内容匹配最合适的 bot",
    )

    topK: int = Field(
        default=5,
        ge=1,
        le=20,
        description="返回的推荐 bot 数量",
    )

    driver_bot_id: Optional[str] = Field(
        default=None,
        description="主导 Bot 的 profile_key(可选),如果提供,该 bot 会作为后续 Fusion 的 driver",
    )

    group_id: Optional[str] = Field(
        default=None,
        description="群组 ID(可选),如果提供,系统会获取群组最近上下文来优化问题推荐",
    )

    min_score: float = Field(
        default=0.01,
        ge=0.0,
        le=1.0,
        description="推荐分数阈值,低于此分数的 bot 不会被返回(范围 0-1)",
    )

    # 向量搜索高级参数
    expand_factor: int = Field(
        default=10,
        ge=1,
        le=10,
        description="扩大召回倍数（向量搜索），默认为 10",
    )

    enable_rerank: bool = Field(
        default=True,
        description="是否启用 Reranker，默认为 True（开启）",
    )

    reranker_model: Optional[str] = Field(
        default=None,
        description="Reranker 模型名（None=使用全局配置）",
    )

    filters: Optional[dict[str, Any]] = Field(
        default=None,
        description="元数据过滤条件，默认只搜索 protected 和 public 的 profile",
    )

    type: Literal["search", "recommend"] = Field(
        default="recommend",
        description="查询类型：search=关键词搜索，recommend=系统推荐（默认）",
    )

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "examples": [
                {
                    "question": "电商大促活动技术方案的风险评估",
                    "topK": 5,
                    "driver_bot_id": "wrk_tech_lead_001:default",
                    "min_score": 0.01,
                    "group_id": "grp_abc123",
                    "type": "recommend",
                },
                {
                    "question": "简单的问候",
                    "topK": 3,
                    "enable_rerank": False,
                    "type": "search",
                }
            ]
        },
    }


class BotRecommendation(BaseModel):
    """
    Bot 推荐结果

    Attributes:
        profile_key: Profile 唯一键
        worker_id: Worker ID (staff_id)
        score: 推荐分数 (0-1)
        reasons: 推荐理由列表
        short_profile: 精简画像（30字以内）
    """

    profile_key: str = Field(
        min_length=1,
        description="Profile 唯一键",
    )

    worker_id: str = Field(
        min_length=1,
        description="Worker ID (staff_id)",
    )

    score: float = Field(
        ge=0.0,
        le=1.0,
        description="推荐分数 (0-1)",
    )

    reasons: list[Union[str, dict[str, Any]]] = Field(
        default_factory=list,
        description="推荐理由列表，支持纯文本字符串或结构化对象",
    )

    short_profile: str = Field(
        default="",
        description="精简画像（30字以内），用于快速展示",
    )

    profile_tags: dict[str, str] = Field(
        default_factory=dict,
        description="Profile 标签字典，如 {\"trust_level\": \"trusted\", \"tag_name\": \"tag_value\"}",
    )


class BotRecommendationResponse(BaseModel):
    """
    Bot 推荐响应

    包含推荐的 Bot 列表。

    Attributes:
        trace_id: 追踪 ID，用于前端埋点关联 query_result 与 bot_select 事件
        type: 查询类型回传：search=关键词搜索，recommend=系统推荐
        driver_bot_id: 建议的 driver bot
        recommendations: 推荐结果列表
    """

    trace_id: str = Field(
        description="追踪 ID，用于前端埋点关联 query_result 与 bot_select 事件",
    )

    type: Literal["search", "recommend"] = Field(
        default="recommend",
        description="查询类型回传：search=关键词搜索，recommend=系统推荐",
    )

    driver_bot_id: Optional[str] = Field(
        default=None,
        description="建议的 driver bot(优先使用请求中的,否则推荐第一个)",
    )

    recommendations: list[BotRecommendation] = Field(
        default_factory=list,
        description="推荐结果列表",
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="诊断 metadata，用于调试和分析",
    )

    model_config = {
        "extra": "forbid",
    }


def create_bot_recommendation_response(
    candidate_response: CandidateRecommendationResponse,
    driver_bot_id: Optional[str] = None,
    trace_id: str = "",
    query_type: str = "recommend",
) -> BotRecommendationResponse:
    """
    从 CandidateRecommendationResponse 创建 BotRecommendationResponse

    Args:
        candidate_response: 候选人推荐响应
        driver_bot_id: 请求中的 driver_bot_id(可选)
        trace_id: 追踪 ID(可选，默认空串)
        query_type: 查询类型(可选，默认 recommend)

    Returns:
        BotRecommendationResponse: Bot 推荐响应
    """
    # 确定 driver_bot_id
    # 优先使用请求中指定的,否则使用第一个推荐(如果有的话)
    actual_driver_bot_id = driver_bot_id
    if actual_driver_bot_id is None and candidate_response.recommendations:
        actual_driver_bot_id = candidate_response.recommendations[0].profile_key

    # 转换为 BotRecommendation
    recommendations = [
        BotRecommendation(
            profile_key=r.profile_key,
            worker_id=r.worker_id,
            score=r.score,
            reasons=r.reasons,
            short_profile=r.short_profile,
            profile_tags={"trust_level": r.trust_level} if r.trust_level else {},
        )
        for r in candidate_response.recommendations
    ]

    return BotRecommendationResponse(
        trace_id=trace_id,
        type=query_type,
        driver_bot_id=actual_driver_bot_id,
        recommendations=recommendations,
        metadata=candidate_response.metadata,
    )


__all__ = [
    "BotRecommendationRequest",
    "BotRecommendation",
    "BotRecommendationResponse",
    "create_bot_recommendation_response",
]