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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.domain.models.metadata_record import MetadataRecord
from src.domain.services.profile_fragment_decomposer import ProfileFragmentDecomposer
from src.domain.services.vector_store_adapter import VectorStoreAdapter
from src.domain.services.metadata_store_adapter import MetadataStoreAdapter

if TYPE_CHECKING:
    from src.domain.services.adapters.worker_profile_filter_adapter import WorkerProfileFilterAdapter


from src.application.services.worker_vector_match_fragments import FragmentMatchingMixin
from src.application.services.worker_vector_match_types import MatchResult, FragmentProfileCandidate as FragmentProfileCandidate

logger = logging.getLogger(__name__)

from src.domain.services.retrieval_logging import log_stage, log_candidates


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




class WorkerVectorMatchService(FragmentMatchingMixin):
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
                logger.debug(
                    "[CONTENT-RELOAD] Successfully loaded content for %s | "
                    "reload_worker_id=%s, reload_profile_id=%s, "
                    "content_length=%d, content_reload_success=true",
                    profile_key,
                    worker_id[:30] if worker_id else "N/A",
                    profile_id[:30] if profile_id else "N/A",
                    len(content),
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
            log_stage(logger, "filters", **default_visibility_filters,
                      filter_fields=sorted(default_visibility_filters))
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

        log_stage(logger, "filters", runtime_state=merged_filters.get("runtime_state"),
                  availability=merged_filters.get("availability"),
                  filter_fields=sorted(merged_filters))

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

        logger.debug(
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
        score_sample = [{"profile_key": r.profile_key, "score": r.score} for r in results[:10]]

        log_candidates(logger, "before_threshold", ((r.profile_key, r.score) for r in results))

        # 应用相似度阈值过滤
        if effective_threshold > 0.0:
            log_candidates(logger, "threshold_removed", (
                (r.profile_key, r.score) for r in results if r.score < effective_threshold
            ))
            filtered_results = [r for r in results if r.score >= effective_threshold]
            removed_count = len(results) - len(filtered_results)
            results = filtered_results
        else:
            removed_count = 0

        log_stage(logger, "threshold", before_count=pre_filter_count, after_count=len(results),
                  removed_count=removed_count, effective_threshold=effective_threshold,
                  vector_min_score=vector_min_score, rerank_min_score=rerank_min_score,
                  before_score_sample=score_sample, sample_truncated=pre_filter_count > 10)
        log_candidates(logger, "final_matches", ((r.profile_key, r.score) for r in results))
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
            log_stage(logger, "registry_filter", before_count=len(results), after_count=len(results), enabled=False)
            return results

        # 获取允许的 profile_keys
        all_profile_keys = [r.profile_key for r in results]
        allowed_keys = self._profile_filter.get_allowed_profile_keys(all_profile_keys)

        # 过滤结果
        filtered_results = [r for r in results if r.profile_key in allowed_keys]
        removed_count = len(results) - len(filtered_results)
        log_stage(logger, "registry_filter", before_count=len(results), after_count=len(filtered_results),
                  removed_count=removed_count, enabled=True)
        log_candidates(logger, "registry_removed", (
            (r.profile_key, r.score) for r in results if r.profile_key not in allowed_keys
        ))
        if removed_count > 0:
            removed = [r.profile_key for r in results if r.profile_key not in allowed_keys]
            logger.debug("[REGISTRY-FILTER] %d -> %d (removed=%d, keys=%s)",
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

        # Preserve the existing provider delegation and fallback policy.
        qdrant_only_fields = {
            "availability", "runtime_state", "fragment_type",
            "test_id", "business_regression", "domain",
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
        seen_keys: set[str] = set()
        for hit in hits:
            # A physical vector ID may end with :full or :skills:N. Identity,
            # exclusions and metadata lookup use the canonical profile key.
            payload = hit.payload or {}
            profile_key = payload.get("profile_key")
            if not profile_key:
                worker_id = payload.get("worker_id") or payload.get("staff_id")
                profile_id = payload.get("profile_id")
                profile_key = f"{worker_id}:{profile_id}" if worker_id and profile_id else hit.id
            if profile_key in seen_keys:
                continue
            if candidate_keys and profile_key not in candidate_keys:
                continue
            if profile_key in excluded_set:
                continue
            metadata = self._metadata_store.get(profile_key)
            if metadata is None:
                # 从 payload 构造基本元数据
                metadata = self._build_metadata_from_payload(profile_key, hit.payload)
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
                profile_key=profile_key,
                metadata=metadata,
                score=hit.score,
                reasons=[f"Vector similarity: {hit.score:.4f}"],
            )
            results.append(result)
            seen_keys.add(profile_key)
            if len(results) >= top_k:
                break

        # Stage 3: Lightweight Rerank
        results = self._rerank(results)

        # Stage 4: Registry State Filter
        results = self._apply_registry_filter(results)

        logger.info("[LEGACY-MATCH] done | result=%d", len(results))

        return results

    # ==================== Fragment 模式 ====================









# 用于 Fragment 模式的数据类


__all__ = [
    "WorkerVectorMatchService",
    "MatchResult",
    "RerankConfig",
    "FragmentRetrievalConfig",
]
