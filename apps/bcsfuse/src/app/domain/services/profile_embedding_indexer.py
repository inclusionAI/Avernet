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
import time
from typing import Any, Optional

from app.domain.models.worker_profile import WorkerProfile
from app.domain.services.profile_fragment_decomposer import ProfileFragmentDecomposer
from app.infra.config.feature_flags import FeatureFlags
from app.infra.indexing.profile_embedding_store import ProfileEmbeddingStore

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
    indexed_profile_keys: list[str] = None
    failed_profile_keys: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        if self.indexed_profile_keys is None:
            self.indexed_profile_keys = []
        if self.failed_profile_keys is None:
            self.failed_profile_keys = []

    def mark_profile_failed(self, profile_key: str) -> None:
        if profile_key not in self.failed_profile_keys:
            self.failed_profile_keys.append(profile_key)

    def mark_profile_indexed(self, profile_key: str) -> None:
        if profile_key not in self.indexed_profile_keys:
            self.indexed_profile_keys.append(profile_key)


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
        batch_size: int = 32,
        inter_batch_delay_seconds: float = 0.25,
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
        self._inter_batch_delay_seconds = inter_batch_delay_seconds
        self._fragment_decomposer = fragment_decomposer or ProfileFragmentDecomposer()
    
    @property
    def batch_size(self) -> int:
        """Return the provider batch size used by index construction."""

        return self._batch_size

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed unique texts once, then map the results back to every fragment.

        Concurrent profile activations and repeated fragments can contain the
        same embedding text. Sending duplicates to the model wastes the shared
        request quota and makes activation bursts much more likely to hit 429.
        """
        if not texts:
            return []

        unique_texts = list(dict.fromkeys(texts))
        embed_batch = getattr(self._embedding_provider, "embed_batch", None)
        if callable(embed_batch):
            unique_vectors = embed_batch(unique_texts)
        else:
            unique_vectors = [
                self._embedding_provider.embed(text) for text in unique_texts
            ]

        if len(unique_vectors) != len(unique_texts):
            raise ValueError(
                "embedding provider returned a mismatched batch count"
            )

        vector_by_text = dict(zip(unique_texts, unique_vectors))
        return [vector_by_text[text] for text in texts]

    def _resolve_worker_state(
        self,
        profile: WorkerProfile,
        worker_states: dict[str, dict] | None,
    ) -> dict | None:
        """Resolve visibility state through the existing candidate lookup order."""

        if not worker_states:
            return None

        candidates: list[str] = []
        staff_id = getattr(profile, "staff_id", None)
        worker_id = getattr(profile, "worker_id", None)
        profile_key = getattr(profile, "profile_key", None)
        profile_id = getattr(profile, "profile_id", None)

        if staff_id:
            candidates.append(staff_id)
        if worker_id:
            candidates.append(worker_id)
        if profile_key:
            candidates.append(profile_key)
            if ":" in profile_key:
                candidates.append(profile_key.split(":", 1)[0])
        if staff_id and profile_id:
            candidates.append(f"{staff_id}:{profile_id}")

        for key in candidates:
            if key in worker_states:
                return worker_states[key]
        return None

    def _profile_fragment_plans(
        self,
        profile: WorkerProfile,
        worker_state: dict | None = None,
    ) -> list[tuple[str, str, dict]]:
        """Build fragment IDs, embedding text, and payload before model work."""

        fragments = self._fragment_decomposer.decompose(profile)
        plans: list[tuple[str, str, dict]] = []

        if not fragments:
            logger.warning(
                "[ProfileEmbeddingIndexer] No fragments generated for %s",
                profile.profile_key,
            )
            return plans

        for fragment in fragments:
            embedding_text = fragment.to_embedding_text()
            if not embedding_text or not embedding_text.strip():
                continue
            fragment_id = fragment.compute_fragment_id(profile.profile_key)

            metadata: dict[str, Any] = {}
            profile_metadata = getattr(profile, "metadata", None)
            if profile_metadata:
                metadata.update(profile_metadata)
            if fragment.metadata:
                metadata.update(fragment.metadata)
            metadata.update(
                self._build_fragment_metadata(fragment, profile, worker_state)
            )
            plans.append((fragment_id, embedding_text, metadata))
        return plans

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
        
        # Build all non-embedding plans first so one provider call can cover
        # fragments from multiple profiles. This is necessary for cold index
        # construction: sequential per-fragment calls cannot use query request
        # coalescing and can exhaust the model request quota.
        plans: list[tuple[WorkerProfile, str, str, dict]] = []
        planned_counts: dict[str, int] = {}
        current_ids_by_profile: dict[str, set[str]] = {}
        worker_ids_by_profile: dict[str, str] = {}

        # 统计有多少 profile 能找到 worker 状态
        found_count = 0
        missing_staff_ids = []

        for i, profile in enumerate(profiles):
            try:
                worker_state = self._resolve_worker_state(profile, worker_states)
                if worker_state:
                    found_count += 1
                elif worker_states and len(missing_staff_ids) < 5:
                    missing_staff_ids.append(
                        f"{profile.staff_id}(profile_key={profile.profile_key})"
                    )

                if i < 3 and worker_states:
                    logger.info(
                        "[WORKER-STATE-TRACE] stage=indexer_lookup, profile_staff_id=%s, "
                        "profile_id=%s, profile_key=%s, found=%s",
                        getattr(profile, "staff_id", None),
                        getattr(profile, "profile_id", None),
                        getattr(profile, "profile_key", None),
                        worker_state is not None,
                    )

                profile_plans = self._profile_fragment_plans(profile, worker_state)
                if profile_plans:
                    current_ids_by_profile[profile.profile_key] = {
                        fragment_id for fragment_id, _, _ in profile_plans
                    }
                    worker_ids_by_profile[profile.profile_key] = profile.staff_id
                if not profile_plans:
                    result.failed_count += 1
                    result.errors.append(
                        f"No embeddable fragments for {profile.profile_key}"
                    )
                    result.mark_profile_failed(profile.profile_key)
                    continue

                for fragment_id, embedding_text, metadata in profile_plans:
                    plans.append((profile, fragment_id, embedding_text, metadata))
                planned_counts[profile.profile_key] = len(profile_plans)

            except Exception as e:
                result.failed_count += 1
                error_msg = f"Error planning {profile.profile_key}: {str(e)}"
                result.errors.append(error_msg)
                result.mark_profile_failed(profile.profile_key)
                logger.error("[ProfileEmbeddingIndexer] %s", error_msg, exc_info=True)

        completed_counts = {profile_key: 0 for profile_key in planned_counts}
        failed_profile_keys: set[str] = set()
        request_count = 0
        for offset in range(0, len(plans), self._batch_size):
            request_count += 1
            if request_count > 1 and self._inter_batch_delay_seconds > 0:
                time.sleep(self._inter_batch_delay_seconds)
            batch = plans[offset : offset + self._batch_size]
            profile_keys = {
                profile.profile_key for profile, _, _, _ in batch
            }
            try:
                vectors = self._embed_texts(
                    [embedding_text for _, _, embedding_text, _ in batch]
                )
                if len(vectors) != len(batch):
                    raise ValueError(
                        "embedding provider returned a mismatched batch count"
                    )
                self._profile_store.upsert_embeddings([
                    (fragment_id, vector, metadata)
                    for (_, fragment_id, _, metadata), vector in zip(batch, vectors)
                ])
                logger.debug(
                    "[ProfileEmbeddingIndexer] Batch indexed: %d/%d fragment plans",
                    min(offset + self._batch_size, len(plans)),
                    len(plans),
                )
            except Exception as e:
                for profile_key in sorted(profile_keys - failed_profile_keys):
                    profile = batch_i = next(
                        candidate
                        for candidate, _, _, _ in batch
                        if candidate.profile_key == profile_key
                    )
                    result.failed_count += 1
                    result.errors.append(f"Error index batch {profile_key}: {str(e)}")
                    result.mark_profile_failed(profile_key)
                    logger.error(
                        "[ProfileEmbeddingIndexer] Batch failed for %s: %s",
                        profile_key,
                        e,
                    )
                    failed_profile_keys.add(profile_key)
                continue

            for profile_key in profile_keys:
                completed_counts[profile_key] += sum(
                    candidate.profile_key == profile_key for candidate, _, _, _ in batch
                )
                if (
                    completed_counts[profile_key] == planned_counts[profile_key]
                    and profile_key not in failed_profile_keys
                ):
                    result.indexed_count += 1
                    result.mark_profile_indexed(profile_key)

        # A successful replacement is not complete while obsolete sparse
        # fragments from the previous profile layout remain searchable. Clean
        # them only after every current fragment for that profile was persisted.
        cleanup_stale = getattr(self._profile_store, "delete_stale_fragments", None)
        if callable(cleanup_stale):
            for profile_key in list(result.indexed_profile_keys):
                try:
                    cleanup_stale(
                        profile_key,
                        current_ids_by_profile.get(profile_key, set()),
                        worker_ids_by_profile.get(profile_key, ""),
                    )
                except Exception as e:
                    error_msg = f"Error cleaning stale fragments {profile_key}: {str(e)}"
                    result.indexed_profile_keys.remove(profile_key)
                    result.indexed_count -= 1
                    result.failed_count += 1
                    result.errors.append(error_msg)
                    result.mark_profile_failed(profile_key)
                    logger.error("[ProfileEmbeddingIndexer] %s", error_msg)

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
    
    def profiles_missing_fragments(
        self,
        profiles: list[WorkerProfile],
        indexed_ids: list[str] | set[str] | None,
    ) -> list[WorkerProfile]:
        """Return profiles whose current fragments are absent from the index.

        A non-empty vector index is not an index-complete signal. Startup can
        be interrupted after a small subset of profiles has been persisted, and
        such a partial index previously caused the backfill to be skipped.

        Passing ``None`` fails closed and returns every profile so callers can
        rebuild rather than silently accepting an index whose IDs cannot be
        inspected.
        """

        if indexed_ids is None:
            return list(profiles)

        indexed = set(indexed_ids)
        missing: list[WorkerProfile] = []
        for profile in profiles:
            expected_ids = {
                fragment.compute_fragment_id(profile.profile_key)
                for fragment in self._fragment_decomposer.decompose(profile)
            }
            if expected_ids - indexed:
                missing.append(profile)
        return missing

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

                    # Fragments can disappear when a profile field becomes
                    # empty (for example, capability tags normalize from
                    # ["无"] to []). Upserts alone leave those old vectors
                    # searchable, so remove IDs that are no longer produced by
                    # the current profile snapshot.
                    current_fragment_ids = {
                        fragment.compute_fragment_id(profile.profile_key)
                        for fragment in new_fragments
                    }
                    stale_fragment_ids = sorted(
                        set(existing_fragments) - current_fragment_ids
                    )
                    if stale_fragment_ids:
                        self._profile_store.delete(stale_fragment_ids)
                        logger.info(
                            "[ProfileEmbeddingIndexer] Deleted %d stale fragments for %s",
                            len(stale_fragment_ids),
                            profile.profile_key,
                        )

                    # 统计复用的数量
                    reused_fragments = len(new_fragments) - len(embeddings_to_upsert)
                    reused_count += max(0, reused_fragments)

                    result.indexed_count += 1

            except Exception as e:
                result.failed_count += 1
                error_msg = f"Error smart updating {profile.profile_key}: {str(e)}"
                result.errors.append(error_msg)
                logger.error("[ProfileEmbeddingIndexer] %s", error_msg, exc_info=True)

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
        Generate one Profile's multi-fragment embeddings.

        The batch indexer uses `_profile_fragment_plans` and groups plans across
        profiles. This method remains for callers that intentionally rebuild a
        single profile, and still uses one provider batch call for that profile.
        """

        plans = self._profile_fragment_plans(profile, worker_state)
        if not plans:
            return []

        try:
            vectors = self._embed_texts(
                [embedding_text for _, embedding_text, _ in plans]
            )
            if len(vectors) != len(plans):
                raise ValueError(
                    "embedding provider returned a mismatched batch count"
                )
        except Exception as e:
            logger.warning(
                "[ProfileEmbeddingIndexer] Failed to generate embeddings for %s: %s",
                profile.profile_key,
                e,
            )
            return []

        return [
            (fragment_id, vector, metadata)
            for (fragment_id, _, metadata), vector in zip(plans, vectors)
        ]

    def delete_by_worker(self, worker_id: str) -> int:
        """Delete exact-owner vectors, including generated or removed profiles."""
        store = self._profile_store.vector_store
        native_delete = getattr(store, "delete_by_worker", None)
        if callable(native_delete):
            return native_delete(worker_id)
        ids = []
        for vector_id in store.get_vector_ids():
            point = store.get(vector_id)
            if point is None:
                continue
            payload = point.get("metadata", point.get("payload", {})) if isinstance(point, dict) else point.payload
            if payload.get("worker_id") == worker_id:
                ids.append(vector_id)
        if ids:
            self._profile_store.delete(ids)
        return len(ids)

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
            get_vector_ids = getattr(inner_store, "get_vector_ids", None)
            if not callable(get_vector_ids):
                native_delete = getattr(inner_store, "delete_by_profile", None)
                if not callable(native_delete):
                    raise AttributeError(
                        "vector store must provide get_vector_ids or delete_by_profile"
                    )
                worker_id, profile_id = profile_key.rsplit(":", 1)
                return native_delete(worker_id, profile_id)

            all_vector_ids = get_vector_ids()

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
                self._profile_store.delete(matching_ids)
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
