"""Fragment matching and reranking implementation for WorkerVectorMatchService."""
from __future__ import annotations
import logging
from time import perf_counter
from collections import defaultdict
from typing import Any
from src.domain.models.metadata_record import MetadataRecord
from src.domain.models.profile_fragment import (
    AggregationStrategy,
    FragmentMatch,
)
from src.domain.models.vector_search_hit import VectorSearchHit
from src.domain.services.profile_fragment_decomposer import ProfileFragmentDecomposer
from src.domain.services.retrieval_logging import log_stage, log_candidates, log_rows
from src.application.services.worker_vector_match_types import MatchResult, FragmentProfileCandidate
from src.application.services.fragment_candidate_selection import select_rerank_candidates

logger = logging.getLogger("src.application.services.worker_vector_match_service")

class FragmentMatchingMixin:
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
                log_stage(logger, "vector_search", index_size=0, hit_count=0, reason="empty_index")
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
            search_started = perf_counter()
            fragment_hits = self._vector_store.search(query_embedding, top_k=search_k, filters=filters)
            raw_hit_count = len(fragment_hits)
            log_candidates(logger, "vector_hits", ((hit.id, hit.score) for hit in fragment_hits))

            # 过滤掉未启用的 fragment 类型（使用运行时权重决定）
            unfiltered_hits = fragment_hits
            fragment_hits = self._filter_fragment_hits(fragment_hits, enabled_fragment_types)
            retained_ids = {hit.id for hit in fragment_hits}
            log_candidates(logger, "fragment_type_removed", (
                (hit.id, hit.score) for hit in unfiltered_hits if hit.id not in retained_ids
            ), level=logging.INFO)
            log_stage(logger, "vector_search", index_size=vector_size, search_k=search_k,
                      hit_count=raw_hit_count, after_type_filter=len(fragment_hits),
                      duration_ms=round((perf_counter() - search_started) * 1000, 2))

        except Exception as e:
            log_stage(logger, "vector_search", reason="provider_error", error_type=type(e).__name__)
            logger.warning("Fragment search failed: %s", e)
            return []

        # Stage 3: 按 profile_key 聚合（使用运行时权重）
        aggregated = self._aggregate_fragments(
            fragment_hits,
            strategy=effective_aggregation_strategy,
            runtime_weights=effective_weights,
        )
        if len(aggregated) == 0:
            log_stage(logger, "candidate_selection", candidate_count=0, reason="no_aggregated_candidates")
            logger.warning("[FRAGMENT-MATCH] aggregation returned empty")
            return []

        # 应用 metadata filter 和排除列表
        candidates = []
        excluded_by_set = 0
        excluded_by_meta = 0
        log_candidates(logger, "candidate_excluded", (
            (key, data["final_score"]) for key, data in aggregated.items() if key in excluded_set
        ), level=logging.INFO)
        log_candidates(logger, "candidate_metadata_removed", (
            (key, data["final_score"]) for key, data in aggregated.items()
            if key not in excluded_set and candidate_keys is not None and key not in candidate_keys
        ), level=logging.INFO)
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
            sorted_candidates, selection_stats = select_rerank_candidates(
                candidates, effective_expand_factor * top_k,
            )
            rerank_input_count = len(sorted_candidates)
            log_stage(logger, "candidate_selection", aggregated_count=len(aggregated),
                      candidate_count=len(candidates), excluded_count=excluded_by_set,
                      metadata_removed_count=excluded_by_meta, rerank_enabled=True,
                      rerank_input_count=rerank_input_count, **selection_stats)
            log_candidates(logger, "reranker_input", ((c.profile_key, c.aggregated_score) for c in sorted_candidates))
            results = self._execute_rerank(
                query=query,
                candidates=sorted_candidates,
                top_k=top_k,
                reranker_model=effective_reranker_model,
                reranker_fail_action=effective_reranker_fail_action,
            )
        else:
            log_stage(logger, "candidate_selection", aggregated_count=len(aggregated),
                      candidate_count=len(candidates), excluded_count=excluded_by_set,
                      metadata_removed_count=excluded_by_meta, rerank_enabled=False,
                      rerank_input_count=0)
            # 按 aggregated_score 降序排序后再截断
            sorted_candidates = sorted(
                candidates,
                key=lambda x: x.aggregated_score,
                reverse=True
            )
            results = self._build_results_from_aggregation(sorted_candidates[:top_k])

        log_candidates(logger, "eligible_candidates", ((c.profile_key, c.aggregated_score) for c in candidates))
        log_candidates(logger, "ranked_results", ((r.profile_key, r.score) for r in results))

        # Stage 5: Lightweight Rerank
        results = self._rerank(results)

        # Stage 6: Registry State Filter
        results = self._apply_registry_filter(results)

        after_rerank_count = len(results)
        final_count = len(results[:top_k])

        logger.debug(
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
            returned_rows = []
            for result in rerank_results:
                if hasattr(result, "profile_key"):
                    key, score = result.profile_key, result.final_score
                    rerank_metadata = getattr(result, "rerank_metadata", {})
                else:
                    key, score = result["profile_key"], result.get("final_score", 0.0)
                    rerank_metadata = result.get("rerank_metadata", {})
                score_source = "aggregate_fallback" if rerank_metadata.get("degraded") else "reranker"
                returned_rows.append([key, round(score, 6), score_source])
            returned_scores = [(row[0], row[1]) for row in returned_rows]
            returned_keys = {key for key, _ in returned_scores}
            log_rows(logger, "reranker_returned", ["profile_key", "score", "score_source"], returned_rows)
            log_rows(logger, "reranker_not_returned", ["profile_key", "weighted_score"], (
                [c.profile_key, round(c.aggregated_score, 6)]
                for c in candidates if c.profile_key not in returned_keys
            ))
            results = self._build_results_from_rerank(rerank_results, candidates)
            built_keys = {result.profile_key for result in results}
            log_candidates(logger, "result_build_removed", (
                (key, score) for key, score in returned_scores if key not in built_keys
            ), level=logging.INFO)
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

        logger.debug(
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
                logger.debug("[VECTOR-MATCH-Agg] Saved metadata for %r", profile_key)

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
        logger.debug(
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
                logger.debug(
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
                    logger.debug("[VECTOR-MATCH-Rerank] Created MetadataRecord for %r", profile_key)
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
                logger.debug(
                    "[MATCH-BUILD-RERANK] MatchResult[%d] | profile_key=%s | score=%.4f | is_reranked=%s",
                    idx, profile_key, result.score, result.is_reranked
                )

        logger.debug(
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
                        logger.debug("[VECTOR-MATCH-Build] Created MetadataRecord for %r", candidate.profile_key)
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
