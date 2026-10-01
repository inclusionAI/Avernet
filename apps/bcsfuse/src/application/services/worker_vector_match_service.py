"""Worker Vector Match Service.

封装三段式查询链路：
1. metadata filter
2. vector ANN search
3. lightweight rerank

Stage 1 Phase 4: 支持 Registry 状态过滤
- 可选注入 WorkerProfileFilterAdapter
- 只返回 active + online 的 profile

V2 改造: 支持 Fragment 多向量检索 + Reranker 精排
- Fragment 级检索
- 按 profile_key 聚合
- Reranker 精排（可选）

不负责：
- embedding 生成
- participants sufficiency 检查
- recommendation 决策

==================================================
行为约定：
==================================================

1. filters 语义：
   - 不同字段之间 = AND 语义
   - 同一字段的 list 值 = contains-any / OR 语义

2. excluded_profile_keys 语义：
   - 在最终结果里必须剔除

3. vector store 失败语义：
   - graceful degradation，返回空列表 []

==================================================
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.domain.models.metadata_record import MetadataRecord
from src.domain.models.profile_fragment import (
    AggregationStrategy,
    FragmentAggregatedResult,
    FragmentMatch,
)
from src.domain.models.vector_search_hit import VectorSearchHit
from src.domain.services.profile_fragment_decomposer import ProfileFragmentDecomposer
from src.domain.services.vector_store_adapter import VectorStoreAdapter
from src.domain.services.metadata_store_adapter import MetadataStoreAdapter

if TYPE_CHECKING:
    from src.domain.services.adapters.worker_profile_filter_adapter import WorkerProfileFilterAdapter


logger = logging.getLogger(__name__)


@dataclass
class RerankConfig:
    """轻量级 Rerank 配置（保留以兼容现有调用）。"""
    pass


@dataclass
class FragmentRetrievalConfig:
    """Fragment 检索配置。

    用于控制 Fragment 多向量检索和 Reranker 精排的行为。
    """
    # 核心开关
    enable_fragment_embedding: bool = False  # 是否启用 Fragment 模式

    # 聚合配置
    aggregation_strategy: str = "weighted_sum"  # 聚合策略（默认：加权求和）

    # 精排配置（配了 RERANKER_MODEL 即启用）
    reranker_model: str | None = None  # Reranker 模型（None=不启用精排）
    reranker_fail_action: str = "degrade"  # 精排失败策略: degrade/empty

    # 扩大召回配置
    expand_factor: int = 2  # 扩大召回倍数

    @classmethod
    def from_env(cls) -> "FragmentRetrievalConfig":
        """从环境变量加载配置（带默认值）"""
        return cls(
            enable_fragment_embedding=os.getenv(
                "ENABLE_FRAGMENT_EMBEDDING", "false"
            ).lower() == "true",
            reranker_model=os.getenv("RERANKER_MODEL"),  # None 表示不启用精排
            expand_factor=int(os.getenv("FRAGMENT_EXPAND_FACTOR", "2")),
            reranker_fail_action=os.getenv("RERANKER_FAIL_ACTION", "degrade"),
            aggregation_strategy=os.getenv(
                "FRAGMENT_AGGREGATION_STRATEGY", cls.aggregation_strategy
            ),
        )

    @property
    def enable_rerank(self) -> bool:
        """是否启用精排（由 reranker_model 配置决定）"""
        return self.reranker_model is not None and self.reranker_model.strip() != ""


@dataclass
class MatchResult:
    """匹配结果（V2 扩展）。

    Attributes:
        profile_key: Profile 唯一标识
        metadata: 完整的元数据记录
        score: 最终得分（向量相似度 + rerank 加分）
        aggregated_score: 【Fragment模式】聚合前的原始分数
        reasons: 得分原因说明列表
        fragment_matches: 【新增】匹配的 fragments 列表（用于解释）
        is_reranked: 【新增】是否经过 Reranker 精排
    """
    profile_key: str
    metadata: MetadataRecord
    score: float
    aggregated_score: float | None = None
    reasons: list[str] = field(default_factory=list)
    fragment_matches: list[FragmentMatch] = field(default_factory=list)
    is_reranked: bool = False


class WorkerVectorMatchService:
    """Worker 向量匹配服务。

    职责：
    - 封装 metadata filter + vector search + rerank 三段链路
    - 支持 Fragment 多向量检索（V2）
    - 支持 Reranker 精排（V2）

    Stage 1 Phase 4:
    - 支持 Registry 状态过滤
    - 只返回 active + online 的 profile

    不负责：
    - embedding 生成（由调用方提供）
    - participants sufficiency 检查
    - recommendation 决策
    """

    def __init__(
        self,
        vector_store: VectorStoreAdapter,
        metadata_store: MetadataStoreAdapter,
        rerank_config: RerankConfig | None = None,
        profile_filter: "WorkerProfileFilterAdapter" | None = None,
        fragment_config: FragmentRetrievalConfig | None = None,
        profile_content_store: Any | None = None,
    ):
        """
        初始化 WorkerVectorMatchService。

        Args:
            vector_store: 向量存储适配器
            metadata_store: 元数据存储适配器
            rerank_config: Rerank 配置（已废弃，保留以兼容现有调用）
            profile_filter: Profile 过滤器（可选）
                - None: 不过滤（后向兼容）
                - WorkerProfileFilterAdapter: 根据 Registry 状态过滤
            fragment_config: Fragment 检索配置（可选，从环境变量加载）
            profile_content_store: Profile 内容存储（可选，用于从 MySQL 加载完整 content）
        """
        self._vector_store = vector_store
        self._metadata_store = metadata_store
        self._profile_filter = profile_filter
        self._profile_content_store = profile_content_store

        # V2: Fragment 配置
        self._fragment_config = fragment_config or FragmentRetrievalConfig.from_env()

        # V2: Reranker 服务（配置了 RERANKER_MODEL 才初始化）
        self._reranker_service = None
        if self._fragment_config.enable_rerank:
            try:
                from src.domain.services.fragment_reranker_service import (
                    FragmentRerankerService,
                    RerankFailAction,
                )

                self._reranker_service = FragmentRerankerService(
                    reranker_model=self._fragment_config.reranker_model,
                    fail_action=RerankFailAction(self._fragment_config.reranker_fail_action),
                )
                logger.info(
                    "[VECTOR-MATCH] Rerank enabled with model: %s",
                    self._fragment_config.reranker_model
                )
            except Exception as e:
                logger.warning("[VECTOR-MATCH] Failed to initialize reranker: %s", e)
                self._reranker_service = None
        else:
            logger.info("[VECTOR-MATCH] Rerank disabled")

        # 获取启用的 fragment 类型（用于检索过滤）
        self._enabled_fragment_types = set(ProfileFragmentDecomposer.get_active_types())

    def set_profile_content_store(self, store: Any) -> None:
        """
        设置 Profile Content Store（用于从 MySQL 加载完整 content）

        Args:
            store: MySQLWorkerProfileContentStore 实例
        """
        self._profile_content_store = store
        logger.info("[VECTOR-MATCH] Profile content store injected for fragment content reload")

    def _load_fragment_content_from_mysql(self, profile_key: str, payload: dict | None = None) -> str | None:
        """
        从 MySQL 加载完整的 profile content

        Phase C: 优先使用 payload 中的 worker_id/profile_id，避免 profile_key 解析错误

        Args:
            profile_key: Profile 标识（格式：worker_id:profile_id 或 worker_id:profile_id:fragment_type:index）
            payload: Qdrant payload，包含 worker_id, profile_id, fragment_id, fragment_type 等

        Returns:
            完整的 profile content 字符串，如果加载失败则返回 None
        """
        if not self._profile_content_store:
            logger.debug("[CONTENT-RELOAD] Profile content store not available, skip reload for %s", profile_key)
            return None

        try:
            # Phase C: 优先使用 payload 中的 worker_id 和 profile_id
            worker_id = None
            profile_id = None

            if payload:
                worker_id = payload.get("worker_id") or payload.get("staff_id")
                profile_id = payload.get("profile_id")

            # 如果 payload 没有，尝试从 profile_key 解析（fallback）
            if not worker_id or not profile_id:
                if ":" not in profile_key:
                    logger.warning("[CONTENT-RELOAD] Invalid profile_key format (missing ':'): %s", profile_key)
                    return None

                # worker_id 可能包含冒号，所以从后往前解析
                # 格式：worker_id:profile_id 或 worker_id:profile_id:fragment_type:index
                parts = profile_key.split(":")
                if len(parts) < 2:
                    logger.warning("[CONTENT-RELOAD] Invalid profile_key format: %s", profile_key)
                    return None

                # 已知的 fragment types，用于识别 profile_key 边界
                known_fragment_types = {"full", "soul", "skills", "capabilities", "profile", "skill_sets", "ecb_summary"}

                # 从后往前找已知的 fragment_type，确定 profile_key 边界
                parsed_worker_id = None
                parsed_profile_id = None

                for idx in range(len(parts) - 1, -1, -1):
                    if parts[idx] in known_fragment_types:
                        # 找到 fragment_type，前面的都是 profile_key（worker_id:profile_id）
                        if idx > 0:
                            profile_key_without_fragment = ":".join(parts[:idx])
                            # 现在 profile_key_without_fragment = worker_id:profile_id
                            # 再次从后往前解析
                            profile_parts = profile_key_without_fragment.split(":")
                            if len(profile_parts) >= 2:
                                parsed_worker_id = ":".join(profile_parts[:-1])
                                parsed_profile_id = profile_parts[-1]
                        break
                else:
                    # 没找到 fragment_type，尝试最简单的 worker_id:profile_id 格式
                    parsed_worker_id = ":".join(parts[:-1])
                    parsed_profile_id = parts[-1]

                # 优先使用 payload 中的值，fallback 使用解析值
                worker_id = worker_id or parsed_worker_id
                profile_id = profile_id or parsed_profile_id

            # 从 MySQL 加载 profile content
            content_dict = self._profile_content_store.get(worker_id, profile_id)

            if not content_dict:
                logger.debug("[CONTENT-RELOAD] No profile content found for %s (worker_id=%s, profile_id=%s)",
                             profile_key, worker_id, profile_id)
                return None

            # 提取 content 字段
            content = content_dict.get("content", "")

            if content:
                # Phase C: 安全诊断日志
                logger.info(
                    "[CONTENT-RELOAD] Successfully loaded content for %s | "
                    "reload_worker_id=%s, reload_profile_id=%s, "
                    "content_length=%d, content_hash_prefix=%s, content_reload_success=true",
                    profile_key,
                    worker_id[:30] if worker_id else "N/A",
                    profile_id[:30] if profile_id else "N/A",
                    len(content),
                    content[:16].encode('utf-8').hex() if len(content) >= 16 else content.encode('utf-8').hex()
                )
                return content
            else:
                logger.debug("[CONTENT-RELOAD] Content field is empty for %s (worker_id=%s, profile_id=%s)",
                             profile_key, worker_id, profile_id)
                return None

        except Exception as e:
            logger.warning("[CONTENT-RELOAD] Failed to load content for %s: %s", profile_key, e)
            return None

    def _inject_default_visibility_filters(
        self,
        filters: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        注入默认可见性过滤条件

        Stage 1 Phase 4: 默认过滤器确保 offline/private worker 不出现在结果中

        规则：
        - runtime_state: 只返回 "online" 的 worker（排除 offline/busy/error）
        - availability: 只返回 "public" 或 "protected" 的 worker（排除 private）

        Args:
            filters: 用户提供的过滤器

        Returns:
            合并后的过滤器（包含默认可见性过滤）
        """
        # 默认可见性过滤器
        default_visibility_filters = {
            "runtime_state": ["online"],  # 只返回 online 的 worker
            "availability": ["public", "protected"]  # 排除 private worker
        }

        # 如果用户未提供过滤器，直接返回默认
        if not filters:
            logger.debug(
                "[VISIBILITY-TRACE] stage=inject_default_filters, user_filters=None, "
                "default_filters=%s",
                default_visibility_filters
            )
            return default_visibility_filters

        # 合并用户过滤器和默认过滤器（AND 语义）
        merged_filters = dict(filters)

        # 如果用户指定了 runtime_state，使用用户的（优先级更高）
        if "runtime_state" not in merged_filters:
            merged_filters["runtime_state"] = default_visibility_filters["runtime_state"]
            logger.debug(
                "[VISIBILITY-TRACE] stage=inject_default_filters, field=runtime_state, "
                "user_not_specified=True, using_default=%s",
                default_visibility_filters["runtime_state"]
            )

        # 如果用户指定了 availability，使用用户的（优先级更高）
        if "availability" not in merged_filters:
            merged_filters["availability"] = default_visibility_filters["availability"]
            logger.debug(
                "[VISIBILITY-TRACE] stage=inject_default_filters, field=availability, "
                "user_not_specified=True, using_default=%s",
                default_visibility_filters["availability"]
            )

        logger.info(
            "[VISIBILITY-TRACE] stage=final_filters, user_filters=%s, "
            "default_filters=%s, merged_filters=%s",
            filters,
            default_visibility_filters,
            merged_filters
        )

        return merged_filters

    def match(
        self,
        query_embedding: list[float],
        top_k: int,
        filters: dict[str, Any] | None = None,
        excluded_profile_keys: list[str] | None = None,
        query: str | None = None,
        mode: str = "fragment",
        vector_min_score: float = 0.01,  # Phase B: 向量召回阶段阈值
        rerank_min_score: float | None = None,  # Phase B: rerank 后质量阈值
        min_score: float | None = None,  # DEPRECATED: 向后兼容
        runtime_config: dict[str, Any] | None = None,
        fragment_type_weights: dict[str, float] | None = None,
    ) -> list[MatchResult]:
        """
        执行匹配查询。

        Args:
            query_embedding: 查询向量
            top_k: 返回结果数量
            filters: 元数据过滤条件
            excluded_profile_keys: 排除的 profile keys
            query: 原始查询文本（用于 Reranker）
            mode: 搜索模式 - "legacy" 或 "fragment"（默认 fragment）
            vector_min_score: 向量召回阶段的最小相似度阈值（默认 0.01，避免过早过滤）
            rerank_min_score: rerank 后的最小质量阈值（None 表示不应用额外阈值）
            min_score: (DEPRECATED) 旧参数，向后兼容。优先使用 vector_min_score 和 rerank_min_score。
            runtime_config: 【并发安全】运行时配置，用于临时覆盖全局配置
                - expand_factor: int (1-10)
                - aggregation_strategy: str
                - reranker_model: str | None
                - reranker_fail_action: str
            fragment_type_weights: 【运行时】Fragment 类型权重覆盖，未指定的使用默认值
                例如：{"soul": 3.0, "skills": 2.0}

        Returns:
            匹配结果列表，按 score 降序排列，已应用相似度阈值过滤
        """
        # Phase B: 向后兼容旧的 min_score 参数
        if min_score is not None and rerank_min_score is None:
            # 如果只传了 min_score，则两个阈值都使用它
            vector_min_score = min_score
            rerank_min_score = min_score

        # 如果 rerank_min_score 未设置，使用 vector_min_score 作为兜底
        if rerank_min_score is None:
            rerank_min_score = vector_min_score

        # Stage 1 Phase 4: 注入默认可见性过滤器
        # 确保 offline/private worker 不出现在结果中
        filters = self._inject_default_visibility_filters(filters)

        logger.info(
            "[MATCH-SVC] start | mode=%s, top_k=%d, vector_min_score=%.3f, rerank_min_score=%.3f, query_len=%d, dim=%d",
            mode, top_k, vector_min_score, rerank_min_score, len(query) if query else 0, len(query_embedding)
        )

        results: list[MatchResult] = []

        # 根据 mode 和环境变量选择执行路径
        # mode="auto" 时，使用环境变量配置；mode="legacy"/"fragment" 时强制指定模式
        use_fragment = self._fragment_config.enable_fragment_embedding
        if mode == "legacy":
            use_fragment = False
        elif mode == "fragment":
            use_fragment = True
        # mode="auto" 或其他值时，保持 use_fragment 的默认值（从环境变量读取）

        # 提取运行时配置（并发安全，不修改全局配置）
        effective_config = runtime_config or {}

        if use_fragment:
            results = self._match_with_fragments(
                query=query or "",
                query_embedding=query_embedding,
                top_k=top_k,
                filters=filters,
                excluded_profile_keys=excluded_profile_keys,
                runtime_config=effective_config,
                fragment_type_weights=fragment_type_weights,
            )
        else:
            results = self._match_legacy(
                query_embedding=query_embedding,
                top_k=top_k,
                filters=filters,
                excluded_profile_keys=excluded_profile_keys,
            )

        pre_filter_count = len(results)

        # Phase B: 根据是否启用 rerank 选择合适的阈值
        # - 如果启用了 rerank，使用 rerank_min_score 过滤
        # - 如果未启用 rerank，使用 vector_min_score 过滤
        effective_threshold = rerank_min_score if any(r.is_reranked for r in results) else vector_min_score

        # DIAGNOSTIC: Log scores before threshold
        if results:
            logger.info(
                "[THRESHOLD-BEFORE] before_threshold=%d | vector_min_score=%.4f | rerank_min_score=%.4f | effective_threshold=%.4f | scores=%s",
                pre_filter_count,
                vector_min_score,
                rerank_min_score,
                effective_threshold,
                [f"{r.profile_key.split(':')[-1]}:{r.score:.4f}" for r in results[:5]]
            )

        # 应用相似度阈值过滤
        if effective_threshold > 0.0:
            filtered_results = [r for r in results if r.score >= effective_threshold]
            removed_count = len(results) - len(filtered_results)
            results = filtered_results
        else:
            removed_count = 0

        # DIAGNOSTIC: Log scores after threshold
        logger.info(
            "[THRESHOLD-AFTER] after_threshold=%d | filtered=%d | effective_threshold=%.4f | scores=%s",
            len(results),
            removed_count,
            effective_threshold,
            [f"{r.profile_key.split(':')[-1]}:{r.score:.4f}" for r in results[:5]]
        )

        logger.info(
            "[MATCH-SVC] done | before_threshold=%d, after_threshold=%d (filtered=%d, vector_min_score=%.3f, rerank_min_score=%.3f, effective_threshold=%.3f), "
            "scores=[%s]",
            pre_filter_count, len(results), removed_count, vector_min_score, rerank_min_score, effective_threshold,
            ", ".join(f"{r.profile_key.split(':')[-1]}:{r.score:.4f}" for r in results[:5])
        )
        return results

    def _apply_registry_filter(self, results: list[MatchResult]) -> list[MatchResult]:
        """
        应用 Registry 状态过滤

        Stage 1 Phase 4:
        - 只返回 active + online 的 profile

        Args:
            results: 匹配结果列表

        Returns:
            过滤后的结果列表
        """
        if self._profile_filter is None:
            return results

        # 获取允许的 profile_keys
        all_profile_keys = [r.profile_key for r in results]
        allowed_keys = self._profile_filter.get_allowed_profile_keys(all_profile_keys)

        # 过滤结果
        filtered_results = [r for r in results if r.profile_key in allowed_keys]
        removed_count = len(results) - len(filtered_results)
        if removed_count > 0:
            removed = [r.profile_key for r in results if r.profile_key not in allowed_keys]
            logger.warning("[REGISTRY-FILTER] %d -> %d (removed=%d, keys=%s)",
                           len(results), len(filtered_results), removed_count,
                           str(removed[:5]) + ("..." if len(removed) > 5 else ""))

        return filtered_results

    def _build_metadata_from_payload(self, profile_key: str, payload: dict | None) -> MetadataRecord | None:
        """
        从向量 payload 构造基本元数据

        当 metadata_store 中没有记录时，从 payload 提取基本信息构造 MetadataRecord

        Args:
            profile_key: Profile 唯一标识
            payload: 向量存储的 payload 字段

        Returns:
            MetadataRecord 或 None
        """
        try:
            # 从 payload 提取 worker_id 和 profile_id（如果存在），否则从 profile_key 解析
            # worker_id 可能包含冒号，所以 profile_id 取最后一部分，worker_id 取前面所有部分
            if payload is not None:
                # 优先使用 payload 中存储的值
                staff_id = payload.get("worker_id") or payload.get("staff_id")
                profile_id = payload.get("profile_id")
                domains = payload.get("domains", [])
                active_skills = payload.get("active_skills", [])
                profile_type = payload.get("profile_type", "bot")
                short_profile = payload.get("short_profile", "")  # 新增：精简画像
            else:
                staff_id = None
                profile_id = None
                domains = []
                active_skills = []
                profile_type = "bot"
                short_profile = ""

            # 如果 payload 中没有，从 profile_key 解析
            if not staff_id or not profile_id:
                if ":" in profile_key:
                    parts = profile_key.split(":")
                    if not staff_id:
                        staff_id = ":".join(parts[:-1])
                    if not profile_id:
                        profile_id = parts[-1]

            # 兜底：如果没有 staff_id，使用整个 profile_key
            if not staff_id:
                staff_id = profile_key

            return MetadataRecord(
                profile_key=profile_key,
                staff_id=staff_id,
                profile_id=profile_id,
                profile_type=profile_type,
                domains=domains if isinstance(domains, list) else [],
                active_skill_names=active_skills if isinstance(active_skills, list) else [],
                suitable_roles=[],
                source_root="api",  # 默认来源
                vector_id=None,
                payload=payload if payload else {},
                short_profile=short_profile,  # 新增：精简画像
            )
        except Exception as e:
            logger.warning(f"[VECTOR-MATCH] 从 payload 构造元数据失败: {profile_key}, error={e}")
            return None

    def _rerank(self, results: list[MatchResult]) -> list[MatchResult]:
        """
        轻量级重排。

        按 score 降序排序，不做额外加分。

        Args:
            results: 原始匹配结果

        Returns:
            重排后的结果（按 score 降序）
        """
        if not results:
            return results

        # 按 score 降序排序
        results.sort(key=lambda r: r.score, reverse=True)

        return results

    # ==================== Legacy 模式 ====================

    def _match_legacy(
        self,
        query_embedding: list[float],
        top_k: int,
        filters: dict[str, Any] | None,
        excluded_profile_keys: list[str] | None,
    ) -> list[MatchResult]:
        """Legacy 模式匹配（单向量）"""
        logger.debug("[LEGACY-MATCH] start | dim=%d, top_k=%d", len(query_embedding), top_k)

        excluded_set = set(excluded_profile_keys or [])

        # Stage 1: Metadata Filter
        candidate_keys: set[str] | None = None

        # 检查 filters 是否包含 Qdrant 特有的字段（metadata_store 不支持）
        # Phase 2.3.5: 添加 business metadata 字段（test_id, business_regression, domain 等）
        # 这些字段只存在于 Qdrant payload，不存在于 metadata_store
        qdrant_only_fields = {
            "availability", "runtime_state", "fragment_type",  # 系统 metadata
            "test_id", "business_regression", "domain",  # Business metadata (Phase 2.3.5)
        }
        has_qdrant_only_fields = filters and any(f in filters for f in qdrant_only_fields)

        if filters and not has_qdrant_only_fields:
            try:
                filtered_records = self._metadata_store.filter(filters)
                candidate_keys = {r.profile_key for r in filtered_records}
                if not candidate_keys:
                    logger.warning("[LEGACY-MATCH] metadata filter returned empty")
                    return []
                logger.debug("[LEGACY-MATCH] metadata filter: %d candidates", len(candidate_keys))
            except Exception as e:
                logger.warning("Metadata filter failed: %s", e)
        elif has_qdrant_only_fields:
            logger.debug("[LEGACY-MATCH] using Qdrant pre-filter (skipped metadata_store)")
        else:
            logger.debug("[LEGACY-MATCH] no metadata filter")

        # Stage 2: Vector Search
        try:
            vector_size = self._vector_store.size()
            if vector_size == 0:
                logger.error("[LEGACY-MATCH] vector store is empty")
                return []

            search_k = top_k * 3 if candidate_keys else top_k
            # 传递 filters 启用 Qdrant 前置过滤
            hits = self._vector_store.search(query_embedding, top_k=search_k, filters=filters)
            logger.debug("[LEGACY-MATCH] search | index_size=%d, search_k=%d, hits=%d", vector_size, search_k, len(hits))
        except Exception as e:
            logger.warning("Vector search failed: %s", e)
            return []

        # 组合结果并过滤
        results: list[MatchResult] = []
        for hit in hits:
            if candidate_keys and hit.id not in candidate_keys:
                continue
            if hit.id in excluded_set:
                continue
            metadata = self._metadata_store.get(hit.id)
            if metadata is None:
                # 从 payload 构造基本元数据
                metadata = self._build_metadata_from_payload(hit.id, hit.payload)
                if metadata is None:
                    logger.debug("[VECTOR-MATCH] 无法从 payload 构造元数据: %s", hit.id)
                    continue
            else:
                # metadata_store 有记录，但 short_profile 可能为空，从 payload 补充
                if not getattr(metadata, 'short_profile', None) and hit.payload:
                    payload_short_profile = hit.payload.get('short_profile', '')
                    if payload_short_profile:
                        metadata.short_profile = payload_short_profile

            result = MatchResult(
                profile_key=hit.id,
                metadata=metadata,
                score=hit.score,
                reasons=[f"Vector similarity: {hit.score:.4f}"],
            )
            results.append(result)
            if len(results) >= top_k:
                break

        # Stage 3: Lightweight Rerank
        results = self._rerank(results)

        # Stage 4: Registry State Filter
        results = self._apply_registry_filter(results)

        logger.info("[LEGACY-MATCH] done | result=%d", len(results))

        return results

    # ==================== Fragment 模式 ====================

    def _match_with_fragments(
        self,
        query: str,
        query_embedding: list[float],
        top_k: int,
        filters: dict[str, Any] | None,
        excluded_profile_keys: list[str] | None,
        runtime_config: dict[str, Any] | None = None,
        fragment_type_weights: dict[str, float] | None = None,
    ) -> list[MatchResult]:
        """
        Fragment 模式匹配（V2）

        流程：
        1. Fragment 级检索（扩大召回）
        2. 按 profile_key 聚合
        3. 【可选】Reranker 精排
        4. 轻量级 rerank
        5. Registry 过滤
        """
        # 获取有效的配置值（运行时配置优先于全局配置）
        get_config = lambda key, default: (runtime_config.get(key) if runtime_config else None) or getattr(self._fragment_config, key, default)

        effective_expand_factor = get_config("expand_factor", 2)
        effective_aggregation_strategy = get_config("aggregation_strategy", "weighted_best")
        effective_reranker_model = (runtime_config.get("reranker_model") if runtime_config else None)
        effective_reranker_fail_action = get_config("reranker_fail_action", "degrade")

        # 判断是否启用 rerank（运行时配置可以覆盖）
        # 逻辑：
        # 1. 如果 runtime_config 中明确指定了 reranker_model（包括 None），使用运行时配置
        # 2. 如果 runtime_config 中没有 reranker_model 字段，沿用全局配置
        runtime_has_rerank = runtime_config and runtime_config.get("reranker_model") is not None
        runtime_explicitly_disabled = runtime_config and "reranker_model" in runtime_config and runtime_config.get("reranker_model") is None
        global_enable_rerank = self._fragment_config.enable_rerank and self._reranker_service is not None

        if runtime_has_rerank:
            enable_rerank = True  # 运行时明确启用
        elif runtime_explicitly_disabled:
            enable_rerank = False  # 运行时明确禁用
        else:
            enable_rerank = global_enable_rerank  # 沿用全局配置

        # 使用运行时权重（传入则完全替换默认值）
        if fragment_type_weights:
            effective_weights = dict(fragment_type_weights)
        else:
            effective_weights = dict(ProfileFragmentDecomposer.DEFAULT_TYPE_WEIGHTS)

        # 获取启用的类型（权重 > 0）
        enabled_fragment_types = {t for t, w in effective_weights.items() if w > 0}

        logger.debug(
            "[FRAGMENT-MATCH] config | dim=%d, query_len=%d, top_k=%d, types=%s, expand=%s, rerank=%s",
            len(query_embedding), len(query), top_k, enabled_fragment_types,
            effective_expand_factor, enable_rerank
        )

        excluded_set = set(excluded_profile_keys or [])

        # Stage 1: Metadata Filter
        candidate_keys: set[str] | None = None

        # 检查 filters 是否包含 Qdrant 特有的字段（metadata_store 不支持）
        # 如果包含，跳过 metadata_store 预过滤，直接使用 Qdrant 前置过滤
        # Phase 2.3.5: 添加 business metadata 字段（test_id, business_regression, domain 等）
        # 这些字段只存在于 Qdrant payload，不存在于 metadata_store
        qdrant_only_fields = {
            "availability", "runtime_state", "fragment_type",  # 系统 metadata
            "test_id", "business_regression", "domain",  # Business metadata (Phase 2.3.5)
        }
        has_qdrant_only_fields = filters and any(f in filters for f in qdrant_only_fields)

        if filters and not has_qdrant_only_fields:
            try:
                filtered_records = self._metadata_store.filter(filters)
                candidate_keys = {r.profile_key for r in filtered_records}
                if not candidate_keys:
                    logger.warning("[FRAGMENT-MATCH] metadata filter returned empty")
                    return []
                logger.debug("[FRAGMENT-MATCH] metadata filter: %d candidates", len(candidate_keys))
            except Exception as e:
                logger.warning("Metadata filter failed: %s", e)
        elif has_qdrant_only_fields:
            logger.debug("[FRAGMENT-MATCH] using Qdrant pre-filter (skipped metadata_store)")
        else:
            logger.debug("[FRAGMENT-MATCH] no filters")

        # Stage 2: Fragment 级检索（扩大召回）
        try:
            vector_size = self._vector_store.size()
            if vector_size == 0:
                logger.error("[FRAGMENT-MATCH] vector store is empty")
                return []

            # 计算需要召回的数量（使用运行时配置）
            search_k = self._calculate_search_k(
                top_k=top_k,
                has_filters=filters is not None,
                has_excluded=len(excluded_set) > 0,
                expand_factor=effective_expand_factor,
            )

            # 传递 filters 启用 Qdrant 前置过滤
            fragment_hits = self._vector_store.search(query_embedding, top_k=search_k, filters=filters)

            # 过滤掉未启用的 fragment 类型（使用运行时权重决定）
            fragment_hits = self._filter_fragment_hits(fragment_hits, enabled_fragment_types)
            logger.debug("[FRAGMENT-MATCH] search | index_size=%d, search_k=%d, hits=%d, after_type_filter=%d",
                         vector_size, search_k, len(fragment_hits), len(fragment_hits))

        except Exception as e:
            logger.warning("Fragment search failed: %s", e)
            return []

        # Stage 3: 按 profile_key 聚合（使用运行时权重）
        aggregated = self._aggregate_fragments(
            fragment_hits,
            strategy=effective_aggregation_strategy,
            runtime_weights=effective_weights,
        )
        if len(aggregated) == 0:
            logger.warning("[FRAGMENT-MATCH] aggregation returned empty")
            return []

        # 应用 metadata filter 和排除列表
        candidates = []
        excluded_by_set = 0
        excluded_by_meta = 0
        for profile_key, data in aggregated.items():
            if profile_key in excluded_set:
                excluded_by_set += 1
                continue
            if candidate_keys and profile_key not in candidate_keys:
                excluded_by_meta += 1
                continue

            candidates.append(FragmentProfileCandidate(
                profile_key=profile_key,
                aggregated_score=data["final_score"],
                fragments=data["fragments"],
                metadata=data.get("metadata", {}),
            ))

        if len(candidates) == 0:
            logger.warning("[FRAGMENT-MATCH] all candidates filtered out! excluded=%d, meta_filtered=%d",
                           excluded_by_set, excluded_by_meta)

        # Stage 4: Reranker 精排（如果启用）
        rerank_input_count = 0
        if enable_rerank:
            # 按 aggregated_score 排序，取 expand_factor * top_k 个进入 Reranker
            rerank_candidates_count = min(
                len(candidates),
                effective_expand_factor * top_k
            )
            sorted_candidates = sorted(
                candidates,
                key=lambda x: x.aggregated_score,
                reverse=True
            )[:rerank_candidates_count]
            rerank_input_count = len(sorted_candidates)
            results = self._execute_rerank(
                query=query,
                candidates=sorted_candidates,
                top_k=top_k,
                reranker_model=effective_reranker_model,
                reranker_fail_action=effective_reranker_fail_action,
            )
        else:
            # 按 aggregated_score 降序排序后再截断
            sorted_candidates = sorted(
                candidates,
                key=lambda x: x.aggregated_score,
                reverse=True
            )
            results = self._build_results_from_aggregation(sorted_candidates[:top_k])

        # Stage 5: Lightweight Rerank
        results = self._rerank(results)

        # Stage 6: Registry State Filter
        results = self._apply_registry_filter(results)

        after_rerank_count = len(results)
        final_count = len(results[:top_k])

        logger.info(
            "[FRAGMENT-MATCH] pipeline | vector_search=%d, aggregated=%d, candidates=%d, "
            "rerank_in=%d, after_rerank=%d, result=%d",
            len(fragment_hits), len(aggregated), len(candidates),
            rerank_input_count, after_rerank_count, final_count
        )

        return results[:top_k]

    def _calculate_search_k(
        self,
        top_k: int,
        has_filters: bool,
        has_excluded: bool,
        expand_factor: int | None = None,
    ) -> int:
        """计算需要的召回数量"""
        num_enabled_types = len(self._enabled_fragment_types)
        if num_enabled_types == 0:
            num_enabled_types = 1

        if expand_factor is None:
            expand_factor = self._fragment_config.expand_factor
        filter_compensation = 2.0 if (has_filters or has_excluded) else 1.0
        aggregation_compensation = 1.7

        search_k = int(
            top_k *
            num_enabled_types *
            expand_factor *
            filter_compensation *
            aggregation_compensation
        )

        min_search_k = top_k * num_enabled_types
        max_search_k = top_k * 50

        search_k = max(min_search_k, min(search_k, max_search_k))
        search_k = ((search_k + 9) // 10) * 10

        logger.debug(
            "[FRAGMENT-MATCH] search_k | top_k=%d, types=%d, expand=%d, "
            "filter_comp=%.1f, agg_comp=%.1f => search_k=%d",
            top_k, num_enabled_types, expand_factor,
            filter_compensation, aggregation_compensation, search_k
        )

        return search_k

    def _execute_rerank(
        self,
        query: str,
        candidates: list,
        top_k: int,
        reranker_model: str | None,
        reranker_fail_action: str,
    ) -> list[MatchResult]:
        """
        执行 Reranker 精排（支持运行时覆盖 reranker 模型）

        Args:
            query: 查询文本
            candidates: 候选列表
            top_k: 返回数量
            reranker_model: Reranker 模型名称（运行时指定）
            reranker_fail_action: 失败处理策略

        Returns:
            MatchResult 列表
        """
        reranker = self._reranker_service

        # 如果指定了不同的 reranker_model，需要创建临时 reranker 实例
        if reranker_model and reranker_model != self._fragment_config.reranker_model:
            try:
                from src.domain.services.fragment_reranker_service import (
                    FragmentRerankerService,
                    RerankFailAction,
                )

                reranker = FragmentRerankerService(
                    reranker_model=reranker_model,
                    fail_action=RerankFailAction(reranker_fail_action),
                )
                logger.debug("[FRAGMENT-MATCH] created temp reranker: %s", reranker_model)
            except Exception as e:
                logger.warning("[FRAGMENT-MATCH] temp reranker creation failed: %s, falling back", e)
                reranker = self._reranker_service

        if reranker is None:
            logger.warning("[FRAGMENT-MATCH] reranker unavailable, skipping")
            return self._build_results_from_aggregation(candidates[:top_k])

        try:
            from src.domain.services.fragment_reranker_service import RerankRequest

            rerank_request = RerankRequest(
                query=query,
                candidates=candidates,
                top_k=top_k,
            )
            rerank_results = reranker.rerank(rerank_request)
            results = self._build_results_from_rerank(rerank_results, candidates)
            logger.debug("[FRAGMENT-MATCH] rerank done: %d results", len(results))
            return results
        except Exception as e:
            logger.error("[FRAGMENT-MATCH] rerank failed: %s", e)
            if reranker_fail_action == "empty":
                return []
            return self._build_results_from_aggregation(candidates[:top_k])

    def _filter_fragment_hits(
        self,
        hits: list[VectorSearchHit],
        enabled_types: set[str] | None = None,
    ) -> list[VectorSearchHit]:
        """过滤掉未启用的 fragment 类型

        Args:
            hits: 原始搜索结果
            enabled_types: 启用的类型集合（默认使用全局配置）
        """
        enabled_types = enabled_types or self._enabled_fragment_types
        # 获取已知 fragment 类型，用于从 ID 中识别 fragment_type
        known_fragment_types = ProfileFragmentDecomposer.get_active_types()

        filtered = []
        legacy_hits = []  # 用于存储旧格式数据
        for hit in hits:
            # 从 payload 读取 fragment_type（新数据）
            fragment_type = hit.payload.get("fragment_type") if hit.payload else None

            # 如果 payload 中没有，尝试从 ID 解析（旧数据兼容）
            # 注意: worker_id 可能包含冒号，所以不能简单取 parts[2]
            if not fragment_type and ":" in hit.id:
                parts = hit.id.split(":")
                # 从后往前找已知的 fragment_type
                for part in reversed(parts):
                    if part in known_fragment_types:
                        fragment_type = part
                        break

            # 只保留有明确 fragment_type 且在启用列表中的
            if fragment_type and fragment_type in enabled_types:
                filtered.append(hit)
            elif not fragment_type:
                # 旧格式数据（没有fragment_type），作为"full"类型处理
                # 这是一个兼容性hack，允许旧数据在fragment模式下工作
                legacy_hits.append(hit)
            # 其他的（未启用的类型）全部过滤掉

        # 如果所有数据都是旧格式，则保留它们（作为full类型）
        if not filtered and legacy_hits:
            logger.warning(
                "[FRAGMENT-FILTER] All %d hits are legacy format (no fragment_type). "
                "Treating them as 'full' type for compatibility.",
                len(legacy_hits)
            )
            # 为legacy hits设置fragment_type为"full"
            for hit in legacy_hits:
                if not hit.payload:
                    hit.payload = {}
                hit.payload["fragment_type"] = "full"
            filtered = legacy_hits

        logger.info(
            "[FRAGMENT-FILTER] Filtered %d hits: %d passed, %d legacy, enabled_types=%s",
            len(hits), len(filtered), len(legacy_hits), enabled_types
        )

        return filtered

    def _aggregate_fragments(
        self,
        fragment_hits: list[VectorSearchHit],
        strategy: str,
        runtime_weights: dict[str, float] | None = None,
    ) -> dict:
        """按 profile_key 聚合 Fragments

        Args:
            fragment_hits: Fragment 搜索结果
            strategy: 聚合策略
            runtime_weights: 运行时权重覆盖（传入则优先使用）
        """
        profile_map = defaultdict(lambda: {
            "fragments": [],
            "best_score": 0.0,
            "weighted_sum": 0.0,
            "total_weight": 0.0,
            "metadata": {},
        })

        # 已知 fragment 类型（用于从 vector ID 中识别 profile_key 边界）
        known_fragment_types = ProfileFragmentDecomposer.get_active_types()

        # 限制日志数量，防止刷屏
        log_limit = 20
        for i, hit in enumerate(fragment_hits):
            # 从 hit.id 提取 profile_key
            # 格式: {worker_id}:{profile_id}:{fragment_type}:{index}
            # 注意: worker_id 本身可能包含冒号，所以不能简单取前两部分
            parts = hit.id.split(":")

            # 从后往前找已知的 fragment_type，确定 profile_key 边界
            profile_key = hit.id  # 默认整个 ID
            for idx in range(len(parts) - 1, -1, -1):
                if parts[idx] in known_fragment_types:
                    # 找到 fragment_type，前面的都是 profile_key
                    if idx > 0:
                        profile_key = ":".join(parts[:idx])
                    break
            else:
                # 没找到 fragment_type，尝试从 payload 获取
                if hit.payload:
                    ft_from_payload = hit.payload.get("fragment_type")
                    if ft_from_payload and ft_from_payload in parts:
                        idx = parts.index(ft_from_payload)
                        if idx > 0:
                            profile_key = ":".join(parts[:idx])

            # 获取 fragment 类型
            fragment_type = hit.payload.get("fragment_type", "unknown") if hit.payload else "unknown"

            # 使用运行时权重（传入则使用）否则使用默认配置中的权重
            if runtime_weights and fragment_type in runtime_weights:
                weight = runtime_weights[fragment_type]
                source = "runtime"
            else:
                # 从默认配置获取权重，默认为 1.0
                weight = ProfileFragmentDecomposer.DEFAULT_TYPE_WEIGHTS.get(fragment_type, 1.0)
                source = "default" if fragment_type in ProfileFragmentDecomposer.DEFAULT_TYPE_WEIGHTS else "fallback(1.0)"

            weighted_score = hit.score * weight
            if i < log_limit:
                logger.debug("[FRAGMENT-AGG] %s: type=%s, raw=%.4f, weight=%.2f[%s], weighted=%.4f",
                            profile_key, fragment_type, hit.score, weight, source, weighted_score)
            elif i == log_limit:
                logger.debug("[FRAGMENT-AGG] ... %d more fragments omitted", len(fragment_hits) - log_limit)

            # 优先使用完整 content，如果没有则使用 content_preview
            full_content = hit.payload.get("content", "") if hit.payload else ""
            content_preview = hit.payload.get("content_preview", "") if hit.payload else ""

            # Phase B: Fragment Content Reload - 从 MySQL 加载完整 content
            # Phase C: 传递 payload 优先使用其中的 worker_id/profile_id
            if not full_content and self._profile_content_store:
                loaded_content = self._load_fragment_content_from_mysql(profile_key, hit.payload)
                if loaded_content:
                    full_content = loaded_content
                    if i < log_limit:
                        logger.debug("[FRAGMENT-AGG] Loaded content from MySQL for %s", profile_key)

            fragment_match = FragmentMatch(
                fragment_type=fragment_type,
                fragment_id=hit.id,
                score=hit.score,
                weighted_score=weighted_score,
                content_preview=content_preview,
                content=full_content if full_content else content_preview,  # 优先完整内容
            )

            p = profile_map[profile_key]
            p["fragments"].append(fragment_match)
            p["best_score"] = max(p["best_score"], weighted_score)
            p["weighted_sum"] += weighted_score
            p["total_weight"] += weight

            if not p["metadata"] and hit.payload:
                p["metadata"] = {
                    "worker_id": hit.payload.get("worker_id"),
                    "profile_id": hit.payload.get("profile_id"),
                    "active_skills": hit.payload.get("active_skills", []),
                    "short_profile": hit.payload.get("short_profile", ""),
                }
                logger.debug("[VECTOR-MATCH-Agg] Saved metadata for %s: short_profile='%s'", profile_key, p["metadata"].get("short_profile", ""))

        # 计算最终分数
        for profile_key, data in profile_map.items():
            if strategy == AggregationStrategy.BEST_MATCH:
                data["final_score"] = data["best_score"]
            elif strategy == AggregationStrategy.WEIGHTED_AVG:
                if data["total_weight"] > 0:
                    data["final_score"] = data["weighted_sum"] / data["total_weight"]
                else:
                    data["final_score"] = 0.0
            elif strategy == AggregationStrategy.WEIGHTED_BEST:
                data["final_score"] = data["best_score"]
            elif strategy == AggregationStrategy.WEIGHTED_SUM:
                data["final_score"] = data["weighted_sum"]  # 加权分数求和
            else:
                data["final_score"] = data["weighted_sum"]  # 默认：求和

        return profile_map

    def _build_results_from_rerank(
        self,
        rerank_results: list,
        candidates: list,
    ) -> list[MatchResult]:
        """从 Rerank 结果构建 MatchResult"""
        from src.domain.services.fragment_reranker_service import RerankResult

        results = []
        candidate_map = {c.profile_key: c for c in candidates}

        # DIAGNOSTIC: Log incoming rerank results
        logger.info(
            "[MATCH-BUILD-RERANK] Incoming rerank_results count=%d | candidates_count=%d",
            len(rerank_results), len(candidates)
        )

        for idx, rr in enumerate(rerank_results):
            if isinstance(rr, RerankResult):
                profile_key = rr.profile_key
                score = rr.final_score
                original_score = rr.original_score
            else:
                # 兼容 dict 格式
                profile_key = getattr(rr, "profile_key", None) or rr.get("profile_key")
                score = getattr(rr, "final_score", None) or rr.get("final_score", 0.0)
                original_score = getattr(rr, "original_score", None) or rr.get("original_score", 0.0)

            # DIAGNOSTIC: Log first 3 results
            if idx < 3:
                logger.info(
                    "[MATCH-BUILD-RERANK] rerank_result[%d] | profile_key=%s | final_score=%.4f | original_score=%.4f",
                    idx, profile_key, score, original_score
                )

            candidate = candidate_map.get(profile_key)
            if not candidate:
                continue

            metadata = self._metadata_store.get(profile_key)
            # 从 candidate 保存的 metadata 中提取 short_profile
            candidate_short_profile = candidate.metadata.get('short_profile', '') if candidate.metadata else ''

            if metadata is None:
                # 从 candidate.metadata 构造基本元数据（包含 short_profile）
                metadata = self._build_metadata_from_payload(
                    profile_key,
                    candidate.metadata if candidate.metadata else None
                )
                if metadata is None:
                    continue
                if candidate_short_profile:
                    logger.debug("[VECTOR-MATCH-Rerank] Created MetadataRecord with short_profile for %s: '%s'", profile_key, candidate_short_profile)
            else:
                # metadata_store 有记录，但 short_profile 可能为空，从 candidate 补充
                if not getattr(metadata, 'short_profile', None) and candidate_short_profile:
                    metadata.short_profile = candidate_short_profile

            result = MatchResult(
                profile_key=profile_key,
                metadata=metadata,
                score=score,
                aggregated_score=candidate.aggregated_score,
                reasons=[f"Rerank score: {score:.4f}"],
                fragment_matches=candidate.fragments[:5],
                is_reranked=True,
            )
            results.append(result)

            # DIAGNOSTIC: Log final MatchResult for top 3
            if idx < 3:
                logger.info(
                    "[MATCH-BUILD-RERANK] MatchResult[%d] | profile_key=%s | score=%.4f | is_reranked=%s",
                    idx, profile_key, result.score, result.is_reranked
                )

        logger.info(
            "[MATCH-BUILD-RERANK] Final MatchResult count=%d | scores=%s",
            len(results),
            [r.score for r in results[:3]]
        )

        return results

    def _build_results_from_aggregation(
        self,
        candidates: list,
    ) -> list[MatchResult]:
        """从聚合结果构建 MatchResult"""
        results = []

        for candidate in candidates:
            metadata = self._metadata_store.get(candidate.profile_key)
            # 从 candidate 保存的 metadata 中提取 short_profile（优先使用）
            candidate_short_profile = candidate.metadata.get('short_profile', '') if candidate.metadata else ''

            if metadata is None:
                # 从 candidate 保存的 metadata 或 fragments 的 payload 构造
                fragment_metadata = candidate.metadata if candidate.metadata else {}
                # 优先使用 candidate.metadata 中的 short_profile
                metadata = self._build_metadata_from_payload(
                    candidate.profile_key,
                    fragment_metadata if fragment_metadata else None
                )
                if metadata is None:
                    # 从保存的基本信息构造
                    # worker_id 可能包含冒号，所以 profile_id 取最后一部分，worker_id 取前面所有部分
                    parts = candidate.profile_key.split(":")
                    worker_id = ":".join(parts[:-1]) if len(parts) > 1 else candidate.profile_key
                    profile_id = parts[-1] if len(parts) > 1 else "default"
                    metadata = MetadataRecord(
                        profile_key=candidate.profile_key,
                        worker_id=worker_id,
                        profile_id=profile_id,
                        domains=[],
                        active_skill_names=[],
                        short_profile=candidate_short_profile,  # 使用从 candidate.metadata 提取的 short_profile
                    )
                    if candidate_short_profile:
                        logger.debug("[VECTOR-MATCH-Build] Created MetadataRecord with short_profile for %s: '%s'", candidate.profile_key, candidate_short_profile)
            else:
                # metadata_store 有记录，但 short_profile 可能为空，从聚合的 metadata 补充
                if not getattr(metadata, 'short_profile', None) and candidate_short_profile:
                    metadata.short_profile = candidate_short_profile
                    logger.debug("[VECTOR-MATCH-Build] Supplementing short_profile for %s", candidate.profile_key)

            result = MatchResult(
                profile_key=candidate.profile_key,
                metadata=metadata,
                score=candidate.aggregated_score,
                aggregated_score=candidate.aggregated_score,
                reasons=[f"Aggregated score: {candidate.aggregated_score:.4f}"],
                fragment_matches=candidate.fragments[:5],
                is_reranked=False,
            )
            results.append(result)

        return results


# 用于 Fragment 模式的数据类
@dataclass
class FragmentProfileCandidate:
    """Fragment 模式下的候选 Profile"""
    profile_key: str
    aggregated_score: float
    fragments: list[FragmentMatch]
    metadata: dict = field(default_factory=dict)


__all__ = [
    "WorkerVectorMatchService",
    "MatchResult",
    "RerankConfig",
    "FragmentRetrievalConfig",
]