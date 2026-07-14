"""
Profile Embedding Indexer

Phase E: Profile Embedding 索引构建服务（Fragment 化改造）

职责：
- 对 worker profiles 生成多 Fragment embedding
- 构建并更新向量索引
- 支持离线/在线索引构建
- 索引缺失时优雅降级

V2 改造：
- 从单向量改为多 Fragment 向量
- 每个 Profile 生成多个 Fragment embeddings
- ID 格式: {profile_key}:{fragment_type}
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from src.domain.models.worker_profile import WorkerProfile
from src.domain.services.profile_fragment_decomposer import ProfileFragmentDecomposer
from src.infra.config.feature_flags import FeatureFlags
from src.infra.indexing.profile_embedding_store import ProfileEmbeddingStore

logger = logging.getLogger(__name__)


@dataclass
class IndexingResult:
    """
    索引构建结果
    
    Attributes:
        total_profiles: 总 profile 数量
        indexed_count: 成功索引数量
        failed_count: 失败数量
        skipped_count: 跳过数量
        duration_seconds: 耗时（秒）
        errors: 错误列表
    """
    total_profiles: int = 0
    indexed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    duration_seconds: float = 0.0
    errors: list[str] = None
    
    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class ProfileEmbeddingIndexer:
    """
    Profile Embedding 索引器
    
    负责将 worker profiles 转换为 embedding 并存入向量索引。
    
    工作流程：
    1. 从 profile source 获取 profiles
    2. 对每个 profile 生成 embedding（通过 embedding provider）
    3. 将 embedding 存入 vector store
    
    降级策略：
    - Embedding provider 不可用 → 跳过并记录
    - Vector store 不可用 → 失败并返回错误
    - 单个 profile 失败 → 记录并继续处理其他
    """
    
    def __init__(
        self,
        embedding_provider: Any,  # EmbeddingProvider
        profile_store: ProfileEmbeddingStore,
        batch_size: int = 100,
        fragment_decomposer: ProfileFragmentDecomposer | None = None,
    ):
        """
        初始化索引器

        Args:
            embedding_provider: Embedding provider（用于生成 embedding）
            profile_store: Profile embedding store（用于存储索引）
            batch_size: 批处理大小
            fragment_decomposer: Fragment 分解器（可选，默认使用默认配置）
        """
        self._embedding_provider = embedding_provider
        self._profile_store = profile_store
        self._batch_size = batch_size
        self._fragment_decomposer = fragment_decomposer or ProfileFragmentDecomposer()
    
    def build_index(
        self,
        profiles: list[WorkerProfile],
        clear_existing: bool = False,
        worker_states: dict[str, dict] | None = None,
    ) -> IndexingResult:
        """
        构建 profile embedding 索引

        Args:
            profiles: Worker profiles 列表
            clear_existing: 是否清空现有索引
            worker_states: Worker 状态字典（可选）
                格式: {staff_id: {"availability": "public", "runtime_state": "online"}}

        Returns:
            IndexingResult: 索引构建结果
        """
        start_time = datetime.now()
        result = IndexingResult(total_profiles=len(profiles))
        
        logger.info(
            "[ProfileEmbeddingIndexer] Starting index build: %d profiles, clear_existing=%s",
            len(profiles), clear_existing
        )
        
        # 检查 feature flag
        if not FeatureFlags.is_profile_embedding_index_enabled():
            logger.warning(
                "[ProfileEmbeddingIndexer] Profile embedding index is disabled by feature flag"
            )
            result.skipped_count = len(profiles)
            result.errors.append("Profile embedding index disabled")
            return result
        
        # 清空现有索引
        if clear_existing:
            self._profile_store.clear_all()
            logger.info("[ProfileEmbeddingIndexer] Cleared existing index")
        
        # 批处理
        batch_embeddings = []

        # 统计有多少 profile 能找到 worker 状态
        found_count = 0
        missing_staff_ids = []

        for i, profile in enumerate(profiles):
            try:
                # Phase 2.6.4: Enhanced worker_state lookup with multiple key candidates
                worker_state = None
                lookup_key = None

                if worker_states:
                    # Build lookup candidates in priority order
                    candidates = []

                    # 1. Try staff_id first (most common case)
                    if hasattr(profile, 'staff_id') and profile.staff_id:
                        candidates.append(('staff_id', profile.staff_id))

                    # 2. Try worker_id if available
                    if hasattr(profile, 'worker_id') and profile.worker_id:
                        candidates.append(('worker_id', profile.worker_id))

                    # 3. Try profile_key (full format: "worker_id:profile_type:variant")
                    if hasattr(profile, 'profile_key') and profile.profile_key:
                        candidates.append(('profile_key', profile.profile_key))

                        # 4. Extract worker_id from profile_key (split on ':' and take first part)
                        if ':' in profile.profile_key:
                            extracted_worker_id = profile.profile_key.split(':', 1)[0]
                            candidates.append(('extracted_worker_id', extracted_worker_id))

                    # 5. Last resort: try staff_id:profile_id combination
                    if hasattr(profile, 'staff_id') and hasattr(profile, 'profile_id') and profile.staff_id and profile.profile_id:
                        candidates.append(('staff_id:profile_id', f"{profile.staff_id}:{profile.profile_id}"))

                    # Try each candidate
                    for key_type, key_value in candidates:
                        if key_value in worker_states:
                            worker_state = worker_states[key_value]
                            lookup_key = f"{key_type}={key_value}"
                            break

                if worker_state:
                    found_count += 1
                elif worker_states and len(missing_staff_ids) < 5:
                    missing_staff_ids.append(f"{profile.staff_id}(profile_key={profile.profile_key})")

                # Diagnostic log for first 3 profiles
                if i < 3 and worker_states:
                    logger.info(
                        "[WORKER-STATE-TRACE] stage=indexer_lookup, profile_staff_id=%s, profile_id=%s, profile_key=%s, lookup_candidates=%s, worker_states_keys=%s, found=%s, lookup_key=%s",
                        getattr(profile, "staff_id", None),
                        getattr(profile, "profile_id", None),
                        getattr(profile, "profile_key", None),
                        {
                            "staff_id": getattr(profile, "staff_id", None),
                            "worker_id": getattr(profile, "worker_id", None),
                            "profile_key": getattr(profile, "profile_key", None),
                        },
                        sorted((worker_states or {}).keys())[:20],
                        worker_state is not None,
                        lookup_key
                    )

                # 生成多 Fragment embeddings
                fragment_embeddings = self._generate_profile_fragment_embeddings(profile, worker_state)

                if fragment_embeddings:
                    for fragment_id, embedding, metadata in fragment_embeddings:
                        batch_embeddings.append((fragment_id, embedding, metadata))

                    # 统计：一个 profile 可能生成多个 fragments
                    result.indexed_count += 1
                else:
                    result.failed_count += 1
                    result.errors.append(f"Failed to generate embedding for {profile.profile_key}")

                # 批量写入
                if len(batch_embeddings) >= self._batch_size:
                    self._profile_store.upsert_embeddings(batch_embeddings)
                    batch_embeddings = []
                    logger.debug(
                        "[ProfileEmbeddingIndexer] Batch indexed: %d/%d profiles",
                        i + 1, result.total_profiles
                    )

            except Exception as e:
                result.failed_count += 1
                error_msg = f"Error indexing {profile.profile_key}: {str(e)}"
                result.errors.append(error_msg)
                logger.error("[ProfileEmbeddingIndexer] %s", error_msg)

        # 写入剩余的 embeddings
        if batch_embeddings:
            self._profile_store.upsert_embeddings(batch_embeddings)

        # 计算耗时
        result.duration_seconds = (datetime.now() - start_time).total_seconds()

        # 打印 worker 状态统计（帮助诊断）
        if worker_states:
            total_profiles = len(profiles)
            logger.info(
                "[ProfileEmbeddingIndexer] Worker state stats: "
                "found=%d/%d (%.1f%%), missing_staff_ids=%s",
                found_count, total_profiles, 100 * found_count / total_profiles if total_profiles else 0,
                missing_staff_ids
            )

        logger.info(
            "[ProfileEmbeddingIndexer] Index build completed: "
            "total=%d, indexed=%d, failed=%d, duration=%.2fs",
            result.total_profiles, result.indexed_count, result.failed_count,
            result.duration_seconds
        )
        
        return result
    
    def update_index(
        self,
        profiles: list[WorkerProfile],
        worker_states: dict[str, dict] | None = None,
    ) -> IndexingResult:
        """
        更新 profile embedding 索引（增量）

        简单版本：直接重新计算所有 embeddings。
        如需智能更新（避免重复计算），请使用 update_index_smart。

        Args:
            profiles: 需要更新的 profiles
            worker_states: Worker 状态字典（可选）
                格式: {staff_id: {"availability": "public", "runtime_state": "online"}}

        Returns:
            IndexingResult: 索引更新结果
        """
        return self.build_index(profiles, clear_existing=False, worker_states=worker_states)

    def update_index_smart(
        self,
        profiles: list[WorkerProfile],
        worker_states: dict[str, dict] | None = None,
    ) -> IndexingResult:
        """
        智能更新 profile embedding 索引（避免重复计算）

        优化策略：
        1. 计算每个 fragment 的 content_hash
        2. 从 vector store 获取已有 fragment embeddings
        3. 对比新旧 fragments：
           - content_hash 相同 → 复用已有 embedding，只更新 worker_state 标签
           - content_hash 不同 → 重新计算 embedding
        4. 如果只更新 payload 中的标签（如 runtime_state, availability），
           则复用所有现有 embeddings，只更新 metadata

        Args:
            profiles: 需要更新的 profiles
            worker_states: Worker 状态字典（可选）
                格式: {staff_id: {"availability": "public", "runtime_state": "online"}}

        Returns:
            IndexingResult: 索引更新结果
        """
        from datetime import datetime
        start_time = datetime.now()
        result = IndexingResult(total_profiles=len(profiles))

        logger.info(
            "[ProfileEmbeddingIndexer] Starting smart index update: %d profiles",
            len(profiles)
        )

        # 检查 feature flag
        if not FeatureFlags.is_profile_embedding_index_enabled():
            logger.warning(
                "[ProfileEmbeddingIndexer] Profile embedding index is disabled by feature flag"
            )
            result.skipped_count = len(profiles)
            result.errors.append("Profile embedding index disabled")
            return result

        # 统计
        reused_count = 0
        recalculated_count = 0
        payload_only_updates = 0

        for profile in profiles:
            try:
                # Step 1: Get worker state with enhanced lookup (Phase 2.6.4)
                worker_state = None
                lookup_key = None

                if worker_states:
                    # Build lookup candidates in priority order
                    candidates = []

                    # 1. Try staff_id first (most common case)
                    if hasattr(profile, 'staff_id') and profile.staff_id:
                        candidates.append(('staff_id', profile.staff_id))

                    # 2. Try worker_id if available
                    if hasattr(profile, 'worker_id') and profile.worker_id:
                        candidates.append(('worker_id', profile.worker_id))

                    # 3. Try profile_key (full format: "worker_id:profile_type:variant")
                    if hasattr(profile, 'profile_key') and profile.profile_key:
                        candidates.append(('profile_key', profile.profile_key))

                        # 4. Extract worker_id from profile_key (split on ':' and take first part)
                        if ':' in profile.profile_key:
                            extracted_worker_id = profile.profile_key.split(':', 1)[0]
                            candidates.append(('extracted_worker_id', extracted_worker_id))

                    # 5. Last resort: try staff_id:profile_id combination
                    if hasattr(profile, 'staff_id') and hasattr(profile, 'profile_id') and profile.staff_id and profile.profile_id:
                        candidates.append(('staff_id:profile_id', f"{profile.staff_id}:{profile.profile_id}"))

                    # Try each candidate
                    for key_type, key_value in candidates:
                        if key_value in worker_states:
                            worker_state = worker_states[key_value]
                            lookup_key = f"{key_type}={key_value}"
                            break

                # Step 2: 生成新的 fragments（不计算 embedding）
                new_fragments = self._fragment_decomposer.decompose(profile)
                if not new_fragments:
                    logger.warning(
                        "[ProfileEmbeddingIndexer] No fragments for %s", profile.profile_key
                    )
                    result.skipped_count += 1
                    continue

                # Step 3: 尝试从 vector store 获取现有 fragments
                existing_fragments = self._get_existing_fragments(profile.profile_key)

                # Step 4: 决定更新策略
                if not existing_fragments:
                    # 全新的 profile，走完整构建流程
                    fragment_embeddings = self._generate_profile_fragment_embeddings(
                        profile, worker_state
                    )
                    if fragment_embeddings:
                        self._profile_store.upsert_embeddings(fragment_embeddings)
                        result.indexed_count += 1
                        recalculated_count += len(new_fragments)
                else:
                    # 已有 profile，智能对比
                    updates, payload_updates, embeddings_to_upsert = self._compute_smart_updates(
                        profile,
                        new_fragments,
                        existing_fragments,
                        worker_state,
                    )

                    # 执行更新
                    if payload_updates:
                        # 只更新 payloads（不重新计算 embedding）
                        self._profile_store.update_payloads(payload_updates)
                        payload_only_updates += len(payload_updates)

                    if embeddings_to_upsert:
                        # 重新计算了的 embeddings
                        self._profile_store.upsert_embeddings(embeddings_to_upsert)
                        recalculated_count += len(embeddings_to_upsert)

                    # 统计复用的数量
                    reused_fragments = len(new_fragments) - len(embeddings_to_upsert)
                    reused_count += max(0, reused_fragments)

                    result.indexed_count += 1

            except Exception as e:
                result.failed_count += 1
                error_msg = f"Error smart updating {profile.profile_key}: {str(e)}"
                result.errors.append(error_msg)
                logger.error("[ProfileEmbeddingIndexer] %s", error_msg)

        # 计算耗时
        result.duration_seconds = (datetime.now() - start_time).total_seconds()

        logger.info(
            "[ProfileEmbeddingIndexer] Smart update completed: "
            "total=%d, indexed=%d, failed=%d, "
            "reused=%d, recalculated=%d, payload_only=%d, duration=%.2fs",
            result.total_profiles,
            result.indexed_count,
            result.failed_count,
            reused_count,
            recalculated_count,
            payload_only_updates,
            result.duration_seconds,
        )

        return result

    def _get_existing_fragments(
        self, profile_key: str
    ) -> dict[str, tuple[list[float], dict]]:
        """
        获取 profile 已存在的 fragment embeddings

        Args:
            profile_key: Profile 标识

        Returns:
            Dict mapping fragment_id -> (vector, payload)
        """
        existing = {}
        try:
            # 通过 profile store 获取 fragments
            fragments = self._profile_store.get_fragments_by_profile(profile_key)
            for fragment_id, vector, payload in fragments:
                if vector:  # 只保存有 vector 的
                    existing[fragment_id] = (vector, payload)
        except Exception as e:
            logger.debug(
                "[ProfileEmbeddingIndexer] Failed to get existing fragments for %s: %s",
                profile_key, e
            )
        return existing

    def _compute_smart_updates(
        self,
        profile: WorkerProfile,
        new_fragments: list,
        existing_fragments: dict[str, tuple[list[float], dict]],
        worker_state: dict | None,
    ) -> tuple[list, list[tuple[str, dict]], list]:
        """
        计算智能更新策略

        Args:
            profile: Worker profile
            new_fragments: 新生成的 fragments
            existing_fragments: 已有的 fragments {fragment_id: (vector, payload)}
            worker_state: Worker 状态

        Returns:
            (updates_info, payload_only_updates, embeddings_to_upsert)
        """
        payload_only_updates: list[tuple[str, dict]] = []
        embeddings_to_upsert: list[tuple[str, list[float], dict]] = []
        updates_info: list[dict] = []

        # 提取现有 worker_state 用于比较
        existing_worker_state = {}
        for frag_id, (_, payload) in existing_fragments.items():
            existing_worker_state = {
                "availability": payload.get("availability"),
                "runtime_state": payload.get("runtime_state"),
            }
            break  # 只需要一个 sample

        # Worker state 是否变化
        worker_state_changed = worker_state != existing_worker_state if worker_state else False

        for fragment in new_fragments:
            fragment_id = fragment.compute_fragment_id(profile.profile_key)

            if fragment_id in existing_fragments:
                # 该 fragment 已存在
                existing_vector, existing_payload = existing_fragments[fragment_id]
                existing_hash = existing_payload.get("content_hash")

                if existing_hash and existing_hash == fragment.content_hash:
                    # Content 完全没变
                    if worker_state_changed:
                        # 只需要更新 worker_state 标签
                        new_payload = dict(existing_payload)
                        if worker_state:
                            new_payload.update({
                                "availability": worker_state.get("availability"),
                                "runtime_state": worker_state.get("runtime_state"),
                            })
                        new_payload["indexed_at"] = datetime.now().isoformat()
                        payload_only_updates.append((fragment_id, new_payload))
                        updates_info.append({
                            "fragment_id": fragment_id,
                            "action": "update_payload",
                            "reason": "worker_state_changed",
                        })
                    else:
                        # 完全没变化，跳过
                        updates_info.append({
                            "fragment_id": fragment_id,
                            "action": "skip",
                            "reason": "no_change",
                        })
                else:
                    # Content 变了，需要重新计算 embedding
                    embedding = self._embedding_provider.embed(fragment.to_embedding_text())
                    if embedding:
                        metadata = self._build_fragment_metadata(fragment, profile, worker_state)
                        embeddings_to_upsert.append((fragment_id, embedding, metadata))
                        updates_info.append({
                            "fragment_id": fragment_id,
                            "action": "recalculate",
                            "reason": "content_changed",
                        })
            else:
                # 全新的 fragment
                embedding = self._embedding_provider.embed(fragment.to_embedding_text())
                if embedding:
                    metadata = self._build_fragment_metadata(fragment, profile, worker_state)
                    embeddings_to_upsert.append((fragment_id, embedding, metadata))
                    updates_info.append({
                        "fragment_id": fragment_id,
                        "action": "create",
                        "reason": "new_fragment",
                    })

        return updates_info, payload_only_updates, embeddings_to_upsert

    def _build_fragment_metadata(
        self,
        fragment,
        profile: WorkerProfile,
        worker_state: dict | None,
    ) -> dict:
        """
        构建 fragment metadata

        Args:
            fragment: ProfileFragment
            profile: WorkerProfile
            worker_state: Worker 状态

        Returns:
            metadata dict
        """
        from datetime import datetime

        metadata = {
            "profile_key": profile.profile_key,
            "worker_id": profile.staff_id,
            "profile_id": profile.profile_id,
            "profile_type": profile.profile_type.value,
            "fragment_type": fragment.fragment_type,
            "active_skills": [s.name for s in profile.active_skills[:100]],
            "content_preview": fragment.content_preview,
            "content": fragment.get_full_content(),
            "indexed_at": datetime.now().isoformat(),
            "content_hash": fragment.content_hash,
            "short_profile": profile.short_profile,  # 新增：精简画像（30字以内）
        }

        if worker_state:
            if "availability" in worker_state:
                metadata["availability"] = worker_state["availability"]
            if "runtime_state" in worker_state:
                metadata["runtime_state"] = worker_state["runtime_state"]

        return metadata
    
    def _generate_profile_fragment_embeddings(
        self,
        profile: WorkerProfile,
        worker_state: dict | None = None,
    ) -> list[tuple[str, list[float], dict]]:
        """
        生成 Profile 的多 Fragment embeddings

        将 Profile 分解为多个语义片段，每个片段生成独立的 embedding。

        Args:
            profile: Worker profile
            worker_state: Worker 状态字典（availability, runtime_state）

        Returns:
            列表，每个元素为 (fragment_id, embedding, metadata)
        """
        results = []

        try:
            # 使用 decomposer 分解 profile
            fragments = self._fragment_decomposer.decompose(profile)

            if not fragments:
                logger.warning(
                    "[ProfileEmbeddingIndexer] No fragments generated for %s",
                    profile.profile_key
                )
                return []

            for fragment in fragments:
                try:
                    # 获取用于 embedding 的文本
                    embedding_text = fragment.to_embedding_text()

                    if not embedding_text.strip():
                        continue

                    # 生成 embedding
                    embedding = self._embedding_provider.embed(embedding_text)

                    if not embedding:
                        continue

                    # 构造 fragment ID
                    # 格式: {profile_key}:{fragment_type}
                    # 如果有 index，则为 {profile_key}:{fragment_type}:{index}
                    if fragment.index > 0:
                        fragment_id = f"{profile.profile_key}:{fragment.fragment_type}:{fragment.index}"
                    else:
                        fragment_id = f"{profile.profile_key}:{fragment.fragment_type}"

                    # 准备 metadata（注：权重不在索引中存储，搜索时动态决定）
                    # Phase 2.3.1 Fix: Merge profile.metadata (business metadata) first
                    metadata = {}

                    # 1. Start with profile.metadata (business metadata: test_id, business_regression, domain, etc.)
                    if hasattr(profile, 'metadata') and profile.metadata:
                        metadata.update(profile.metadata)

                    # Phase 2.3.5: PAYLOAD-TRACE - Log metadata values after merge
                    if 'test_id' in metadata or 'business_regression' in metadata:
                        logger.info(
                            "[PAYLOAD-TRACE] stage=metadata_merged, worker_id=%s, profile_id=%s, "
                            "test_id_present=%s, business_regression_value=%r, business_regression_type=%s, domain=%r",
                            profile.staff_id,
                            profile.profile_id,
                            "test_id" in metadata,
                            metadata.get("business_regression"),
                            type(metadata.get("business_regression")).__name__,
                            metadata.get("domain")
                        )

                    # 2. Add fragment metadata if exists
                    if hasattr(fragment, 'metadata') and fragment.metadata:
                        metadata.update(fragment.metadata)

                    # 3. Set core system fields (these must not be overwritten by business metadata)
                    metadata.update({
                        "profile_key": profile.profile_key,
                        "worker_id": profile.staff_id,
                        "profile_id": profile.profile_id,
                        "profile_type": profile.profile_type.value,
                        "fragment_type": fragment.fragment_type,
                        "active_skills": [s.name for s in profile.active_skills[:100]],
                        "content_preview": fragment.content_preview,  # 200字符预览
                        "content": fragment.get_full_content(),  # 完整内容，供reranker使用
                        "indexed_at": datetime.now().isoformat(),
                        "short_profile": profile.short_profile,  # 精简画像（30字以内）
                    })

                    # 添加 Worker 状态信息（用于前置过滤）
                    if worker_state:
                        if "availability" in worker_state:
                            metadata["availability"] = worker_state["availability"]
                        if "runtime_state" in worker_state:
                            metadata["runtime_state"] = worker_state["runtime_state"]
                        logger.info(
                            "[WORKER-STATE-TRACE] stage=payload_visibility_fields, fragment_id=%s, worker_id=%s, profile_id=%s, runtime_state=%r, availability=%r, lookup_key=%s",
                            fragment_id,
                            metadata.get("worker_id"),
                            metadata.get("profile_id"),
                            metadata.get("runtime_state"),
                            metadata.get("availability"),
                            lookup_key if 'lookup_key' in dir() else 'N/A'
                        )
                    else:
                        logger.warning(
                            "[ProfileEmbeddingIndexer] No worker_state for fragment %s (profile.staff_id=%s, profile_key=%s)",
                            fragment_id,
                            profile.staff_id,
                            profile.profile_key
                        )

                    results.append((fragment_id, embedding, metadata))

                except Exception as e:
                    logger.warning(
                        "[ProfileEmbeddingIndexer] Failed to generate embedding for fragment %s: %s",
                        fragment.fragment_type, str(e)
                    )
                    continue

            logger.debug(
                "[ProfileEmbeddingIndexer] Generated %d fragment embeddings for %s",
                len(results), profile.profile_key
            )

            return results

        except Exception as e:
            logger.error(
                "[ProfileEmbeddingIndexer] Failed to generate fragment embeddings for %s: %s",
                profile.profile_key, str(e)
            )
            return []
    
    def delete_by_profile(self, profile_key: str) -> int:
        """
        删除指定 profile 的所有向量

        支持新旧两种向量 ID 格式：
        - 旧格式: {profile_key} (如 "2088888:default")
        - 新格式: {profile_key}:{fragment_type} (如 "2088888:default:full")
        - 新格式（带索引）: {profile_key}:{fragment_type}:{index}

        Args:
            profile_key: Profile key (格式: {worker_id}:{profile_id})

        Returns:
            删除的向量数量
        """
        logger.info("[ProfileEmbeddingIndexer] Starting delete for profile: %s", profile_key)

        try:
            # 从底层 vector store 获取所有向量 ID
            inner_store = self._profile_store.vector_store
            all_vector_ids = inner_store.get_vector_ids()

            # 匹配两种格式：
            # 1. 精确匹配旧格式: {profile_key}
            # 2. 前缀匹配新格式: {profile_key}:{fragment_type}...
            prefix = f"{profile_key}:"
            matching_ids = [
                vid for vid in all_vector_ids
                if vid == profile_key or vid.startswith(prefix)
            ]

            logger.info(
                "[ProfileEmbeddingIndexer] Found %d matching vectors for profile_key='%s'",
                len(matching_ids), profile_key
            )

            if matching_ids:
                self._profile_store.delete_embeddings(matching_ids)
                logger.info(
                    "[ProfileEmbeddingIndexer] Deleted %d vectors for %s",
                    len(matching_ids), profile_key
                )
            else:
                logger.warning("[ProfileEmbeddingIndexer] No vectors found for %s", profile_key)

            return len(matching_ids)

        except Exception as e:
            logger.error("[ProfileEmbeddingIndexer] Failed to delete vectors for %s: %s", profile_key, e, exc_info=True)
            raise

    def get_index_stats(self) -> dict[str, Any]:
        """
        获取索引统计信息

        Returns:
            索引统计信息
        """
        return {
            "index_size": self._profile_store.size(),
            "index_available": self._profile_store.is_index_available(),
            "dimension": self._profile_store.dimension,
        }


__all__ = [
    "ProfileEmbeddingIndexer",
    "IndexingResult",
]
