"""
Participant Availability Checker

Stage 1 Phase 5: Fusion Offline Participant Warning

检查显式给定的 participant 是否可用（online）。

职责：
1. 从 participant_id (profile_key) 反查 worker_id
2. 检查 worker 的 runtime_state 是否为 online
3. 返回可用性信息

使用场景：
- GroupFusionService 在收集 perspectives 前，检查显式 participants 是否可用
- 对于 offline 的 participant，生成 warning 并创建 skipped perspective

Feature Flags:
- ENABLE_EXPLICIT_PARTICIPANT_AVAILABILITY_WARNING: 控制是否启用警告功能
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from src.domain.models.worker_runtime_state import WorkerRuntimeState
from src.domain.services.adapters.worker_profile_binding_store_adapter import (
    WorkerProfileBindingStoreAdapter,
)
from src.domain.services.adapters.worker_runtime_state_store_adapter import (
    WorkerRuntimeStateStoreAdapter,
)
from src.infra.config.feature_flags import FeatureFlags

logger = logging.getLogger(__name__)


@dataclass
class ParticipantAvailability:
    """
    Participant 可用性信息

    Attributes:
        participant_id: 参与者标识 (profile_key)
        is_available: 是否可用（已注册且 online）
        worker_id: 关联的 Worker ID（如果已注册）
        runtime_state: 当前运行时状态
        is_registered: 是否已注册
        unavailability_reason: 不可用原因（如果不可用）
    """
    participant_id: str
    is_available: bool
    worker_id: Optional[str] = None
    runtime_state: Optional[WorkerRuntimeState] = None
    is_registered: bool = False
    unavailability_reason: Optional[str] = None


class ParticipantAvailabilityChecker:
    """
    Participant 可用性检查器

    检查显式给定的 participant 是否可以参与融合。

    判断规则：
    1. 未注册的 participant → is_available=False, unavailability_reason="unregistered"
    2. 已注册但 offline → is_available=False, unavailability_reason="offline"
    3. 已注册且 online → is_available=True

    注意：这里不检查 lifecycle_state，因为：
    - 如果 worker lifecycle_state != active，理论上不应该在 registry 中
    - runtime_state 是运行时可用性的直接指标
    """

    def __init__(
        self,
        profile_binding_store: WorkerProfileBindingStoreAdapter,
        runtime_state_store: WorkerRuntimeStateStoreAdapter,
        registry_store=None,
    ):
        """
        初始化检查器

        Args:
            profile_binding_store: Profile 绑定存储（用于反查 worker_id）
            runtime_state_store: 运行时状态存储（用于检查 online/offline）
            registry_store: Worker Registry 存储（可选，用于直接查询 worker_id）
        """
        self._profile_binding_store = profile_binding_store
        self._runtime_state_store = runtime_state_store
        self._registry_store = registry_store

    def check_availability(self, participant_id: str) -> ParticipantAvailability:
        """
        检查单个 participant 的可用性

        Args:
            participant_id: 参与者标识（通常是 profile_key 格式）

        Returns:
            ParticipantAvailability: 可用性信息
        """
        logger.info("[G6-AVAILABILITY] ========== check_availability START ==========")
        logger.info("[G6-AVAILABILITY] participant_id: %s", participant_id)
        logger.info("[G6-AVAILABILITY] _profile_binding_store type: %s", type(self._profile_binding_store).__name__)
        logger.info("[G6-AVAILABILITY] _registry_store type: %s", type(self._registry_store).__name__ if self._registry_store else "None")

        # Feature Flag 检查：如果未启用，则所有 participant 视为可用
        if not FeatureFlags.is_explicit_participant_availability_warning_enabled():
            logger.info("[G6-AVAILABILITY] Feature flag disabled, treating as available: %s", participant_id)
            return ParticipantAvailability(
                participant_id=participant_id,
                is_available=True,
                is_registered=False,
            )

        # 1. 尝试从 profile_binding 反查 worker_id
        logger.info("[G6-AVAILABILITY] Step 1: Query binding by profile_key...")
        logger.info("[G6-AVAILABILITY]   Calling get_binding_by_profile_key(%s)", participant_id)
        binding = self._profile_binding_store.get_binding_by_profile_key(participant_id)
        logger.info("[G6-AVAILABILITY]   Binding query result: %s", "FOUND" if binding else "NOT_FOUND")

        worker_id = None
        if binding is not None:
            worker_id = binding.worker_id
            logger.info("[G6-AVAILABILITY] Step 1 SUCCESS: Found binding")
            logger.info("[G6-AVAILABILITY]   binding.worker_id: %s", binding.worker_id)
            logger.info("[G6-AVAILABILITY]   binding.profile_key: %s", binding.profile_key)
            logger.info("[G6-AVAILABILITY]   binding.is_active: %s", binding.is_active)
            logger.info("[G6-AVAILABILITY]   binding.source_type: %s", binding.source_type)
        elif self._registry_store is not None:
            # 尝试直接从 registry_store 查询（支持 worker_id 作为 participant_id）
            logger.info("[G6-AVAILABILITY] Step 2: Fallback to registry_store query...")
            logger.info("[G6-AVAILABILITY]   Calling registry_store.get_by_id(%s)", participant_id)
            try:
                worker = self._registry_store.get_by_id(participant_id)
                logger.info("[G6-AVAILABILITY]   Registry query result: %s", "FOUND" if worker else "NOT_FOUND")
                if worker is not None:
                    worker_id = participant_id
                    logger.info("[G6-AVAILABILITY] Step 2 SUCCESS: Found worker in registry")
                    logger.info("[G6-AVAILABILITY]   worker.id: %s", worker.id)
                    logger.info("[G6-AVAILABILITY]   worker.active_profile_key: %s", getattr(worker, 'active_profile_key', 'N/A'))
                    logger.info("[G6-AVAILABILITY]   worker.lifecycle_state: %s", getattr(worker, 'lifecycle_state', 'N/A'))
                else:
                    logger.warning("[G6-AVAILABILITY] Step 2 FAILED: Worker not found in registry")
            except Exception as e:
                logger.error("[G6-AVAILABILITY] Step 2 EXCEPTION: registry_store.get_by_id(%s) failed: %s", participant_id, e, exc_info=True)
        else:
            logger.warning("[G6-AVAILABILITY] Step 2 SKIPPED: registry_store not injected")

        if worker_id is None:
            # 未注册的 participant
            logger.error("[G6-AVAILABILITY] ❌ PARTICIPANT NOT REGISTERED")
            logger.error("[G6-AVAILABILITY]   participant_id: %s", participant_id)
            logger.error("[G6-AVAILABILITY]   Both binding query and registry query failed")
            logger.error("[G6-AVAILABILITY]   This means:")
            logger.error("[G6-AVAILABILITY]     1. No binding exists with profile_key='%s'", participant_id)
            logger.error("[G6-AVAILABILITY]     2. No worker exists with id='%s'", participant_id)
            logger.error("[G6-AVAILABILITY]   Root cause hypothesis:")
            logger.error("[G6-AVAILABILITY]     - Binding table may not have been created during API lifecycle")
            logger.error("[G6-AVAILABILITY]     - Or profile_key format mismatch (e.g., missing ':default' suffix)")
            logger.error("[G6-AVAILABILITY] ========== check_availability END (UNREGISTERED) ==========")
            return ParticipantAvailability(
                participant_id=participant_id,
                is_available=False,
                is_registered=False,
                unavailability_reason="unregistered",
            )

        logger.info("[G6-AVAILABILITY] ✓ Worker found: participant=%s -> worker=%s", participant_id, worker_id)

        # 2. 检查 worker 的 runtime_state
        logger.info("[G6-AVAILABILITY] Step 3: Check runtime state for worker %s", worker_id)
        logger.info("[G6-AVAILABILITY]   _runtime_state_store type: %s", type(self._runtime_state_store).__name__)
        runtime_state_data = self._runtime_state_store.get_runtime_state(worker_id)
        logger.info("[G6-AVAILABILITY]   Runtime state query result: %s", runtime_state_data if runtime_state_data else "None")

        if runtime_state_data is None:
            # worker 没有运行时状态记录，默认为 offline
            logger.warning("[G6-AVAILABILITY] ⚠️ No runtime state record found")
            logger.warning("[G6-AVAILABILITY]   Assuming worker is OFFLINE")
            logger.warning("[G6-AVAILABILITY] ========== check_availability END (OFFLINE - NO STATE) ==========")
            return ParticipantAvailability(
                participant_id=participant_id,
                is_available=False,
                worker_id=worker_id,
                runtime_state=WorkerRuntimeState.OFFLINE,
                is_registered=True,
                unavailability_reason="offline",
            )

        # Handle both dict (OSS mode) and WorkerRuntimeState enum
        if isinstance(runtime_state_data, dict):
            # OSS mode: runtime_state_data is a dict
            state_value = runtime_state_data.get("state", "offline")
            runtime_state = WorkerRuntimeState(state_value) if state_value in ["online", "offline"] else WorkerRuntimeState.OFFLINE
            logger.info("[G6-AVAILABILITY] Runtime state (dict mode): %s -> %s", state_value, runtime_state.value)
        else:
            # Object mode: runtime_state_data is WorkerRuntimeState enum
            runtime_state = runtime_state_data
            logger.info("[G6-AVAILABILITY] Runtime state (enum mode): %s", runtime_state.value)

        # 3. 判断是否 online
        if runtime_state == WorkerRuntimeState.ONLINE:
            logger.info("[G6-AVAILABILITY] ✅ PARTICIPANT AVAILABLE")
            logger.info("[G6-AVAILABILITY]   participant_id: %s", participant_id)
            logger.info("[G6-AVAILABILITY]   worker_id: %s", worker_id)
            logger.info("[G6-AVAILABILITY]   runtime_state: online")
            logger.info("[G6-AVAILABILITY] ========== check_availability END (ONLINE) ==========")
            return ParticipantAvailability(
                participant_id=participant_id,
                is_available=True,
                worker_id=worker_id,
                runtime_state=runtime_state,
                is_registered=True,
            )

        # offline 或其他状态
        logger.warning("[G6-AVAILABILITY] ⚠️ PARTICIPANT OFFLINE")
        logger.warning("[G6-AVAILABILITY]   participant_id: %s", participant_id)
        logger.warning("[G6-AVAILABILITY]   worker_id: %s", worker_id)
        logger.warning("[G6-AVAILABILITY]   runtime_state: %s", runtime_state.value)
        logger.warning("[G6-AVAILABILITY] ========== check_availability END (OFFLINE) ==========")
        return ParticipantAvailability(
            participant_id=participant_id,
            is_available=False,
            worker_id=worker_id,
            runtime_state=runtime_state,
            is_registered=True,
            unavailability_reason=f"runtime_state={runtime_state.value}",
        )

    def check_batch(self, participant_ids: list[str]) -> dict[str, ParticipantAvailability]:
        """
        批量检查多个 participants 的可用性

        Args:
            participant_ids: 参与者标识列表

        Returns:
            dict: {participant_id: ParticipantAvailability}
        """
        return {
            participant_id: self.check_availability(participant_id)
            for participant_id in participant_ids
        }

    def get_offline_participants(self, participant_ids: list[str]) -> list[ParticipantAvailability]:
        """
        获取所有不可用的 participants

        Args:
            participant_ids: 参与者标识列表

        Returns:
            list[ParticipantAvailability]: 不可用的 participants 列表
        """
        availabilities = self.check_batch(participant_ids)
        return [a for a in availabilities.values() if not a.is_available]


__all__ = [
    "ParticipantAvailabilityChecker",
    "ParticipantAvailability",
]