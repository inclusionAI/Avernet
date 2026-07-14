"""
Worker Runtime State Service

Worker 运行态管理应用服务。

Stage 1 职责：
- set_online: 设置 worker 为在线状态
- set_offline: 设置 worker 为离线状态
- get_runtime_state: 获取运行态

规则：
- disabled worker 不能设为 online
- inactive worker 不能设为 online
- 状态变化写审计日志
- 状态变化触发 index sync
- worker 下线时删除向量数据（防止脏数据和召回率降低）
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from src.domain.models.worker import Worker
from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
from src.domain.models.worker_runtime_state import WorkerRuntimeState
from src.domain.models.worker import Availability
from src.domain.models.worker_audit_log import WorkerAuditLog, WorkerAuditAction
from src.domain.models.worker_source_info import WorkerSourceType
from src.domain.services.adapters.worker_registry_store_adapter import (
    WorkerRegistryStoreAdapter,
)
from src.domain.services.adapters.worker_runtime_state_store_adapter import (
    WorkerRuntimeStateStoreAdapter,
)
from src.domain.services.adapters.worker_audit_log_adapter import (
    WorkerAuditLogAdapter,
)
from src.domain.services.adapters.worker_index_sync_adapter import (
    WorkerIndexSyncAdapter,
)
from src.domain.exceptions import WorkerNotFoundException

if TYPE_CHECKING:
    from src.infra.indexing.profile_embedding_store import ProfileEmbeddingStore


logger = logging.getLogger(__name__)


class WorkerRuntimeStateService:
    """
    Worker 运行态管理服务

    Stage 1 职责：
    - online/offline 状态管理
    - 约束检查
    - 审计日志
    - 索引同步
    - worker 下线时删除向量数据
    """

    def __init__(
        self,
        registry_store: WorkerRegistryStoreAdapter,
        runtime_state_store: WorkerRuntimeStateStoreAdapter,
        audit_log_adapter: WorkerAuditLogAdapter,
        index_sync_adapter: WorkerIndexSyncAdapter,
        vector_store: Optional["ProfileEmbeddingStore"] = None,
    ):
        """
        初始化服务

        Args:
            registry_store: Worker Registry Store
            runtime_state_store: Runtime State Store
            audit_log_adapter: Audit Log Adapter
            index_sync_adapter: Index Sync Adapter
            vector_store: 向量存储，用于 worker 下线时删除向量数据
        """
        self._registry_store = registry_store
        self._runtime_state_store = runtime_state_store
        self._audit_log_adapter = audit_log_adapter
        self._index_sync_adapter = index_sync_adapter
        self._vector_store = vector_store

    def set_online(
        self,
        worker_id: str,
        updated_by: Optional[str] = None,
    ) -> Worker:
        """
        设置 Worker 为在线状态

        规则：
        - disabled worker 不能设为 online
        - inactive worker 不能设为 online
        - 状态变化写审计日志
        - 状态变化触发 index sync

        Args:
            worker_id: Worker ID
            updated_by: 更新来源

        Returns:
            更新后的 Worker

        Raises:
            WorkerNotFoundException: Worker 不存在
            ValueError: 生命周期状态不允许设为 online
        """
        # 获取 worker
        worker = self._registry_store.get_by_id(worker_id)
        if worker is None:
            raise WorkerNotFoundException(worker_id)

        # Handle both dict and Worker object
        if isinstance(worker, dict):
            # OSS mode: worker is a dict
            # OSS mode doesn't have lifecycle_state in worker dict
            # Skip lifecycle state check for OSS mode
            logger.debug(f"[RuntimeStateService] OSS mode: worker is dict, skipping lifecycle check")
        else:
            # Object mode: worker is a Worker domain object
            # 检查生命周期约束
            if worker.lifecycle_state == WorkerLifecycleState.DISABLED:
                raise ValueError(
                    f"Cannot set worker {worker_id} to online: lifecycle_state is DISABLED"
                )

            if worker.lifecycle_state == WorkerLifecycleState.INACTIVE:
                raise ValueError(
                    f"Cannot set worker {worker_id} to online: lifecycle_state is INACTIVE"
                )

        # 获取当前 runtime state
        old_state = self._runtime_state_store.get_runtime_state(worker_id)
        if old_state is None:
            old_state = WorkerRuntimeState.OFFLINE

        # 如果已经是 online，同步 worker.state 后返回
        if old_state == WorkerRuntimeState.ONLINE:
            # 确保 worker.state.runtime_state 同步
            if isinstance(worker, dict):
                # OSS mode: dict worker
                # Already online, just return
                return worker
            else:
                # Object mode: check and update runtime_state
                if worker.state.runtime_state != WorkerRuntimeState.ONLINE:
                    worker.state.runtime_state = WorkerRuntimeState.ONLINE
                    worker = self._registry_store.update(worker)
                return worker

        # 设置新状态
        self._runtime_state_store.set_runtime_state(
            worker_id=worker_id,
            runtime_state={
                "state": WorkerRuntimeState.ONLINE.value,
                "heartbeat_at": None,
                "metadata": {}
            },
            updated_by=updated_by,
        )

        # Note: runtime_state is managed separately in runtime_state_store
        # No need to update worker object for OSS mode (dict)
        # Only update worker object if it's a Worker domain object
        if not isinstance(worker, dict):
            # Object mode: update Worker object
            worker.state.runtime_state = WorkerRuntimeState.ONLINE
            try:
                worker = self._registry_store.update(worker)
            except Exception as e:
                logger.warning(f"[RuntimeStateService] Failed to update worker object: {e}")
                # Continue anyway - runtime state is already saved

        # Phase C1: Sync denormalized column workers.runtime_state
        # Try to call sync_runtime_state_mirror if registry_store has it
        try:
            if hasattr(self._registry_store, 'sync_runtime_state_mirror'):
                sync_success = self._registry_store.sync_runtime_state_mirror(
                    worker_id=worker_id,
                    runtime_state=WorkerRuntimeState.ONLINE.value,
                )
                if sync_success:
                    logger.info(
                        f"[RuntimeStateService] Synced runtime_state mirror: "
                        f"worker_id={worker_id}, runtime_state=online"
                    )
                else:
                    logger.warning(
                        f"[RuntimeStateService] Failed to sync runtime_state mirror: "
                        f"worker_id={worker_id}, runtime_state=online"
                    )
            else:
                logger.debug(
                    f"[RuntimeStateService] registry_store does not support sync_runtime_state_mirror"
                )
        except Exception as e:
            logger.error(
                f"[RuntimeStateService] Error syncing runtime_state mirror: {e}",
                exc_info=True
            )

        # 记录审计日志
        if self._audit_log_adapter:
            try:
                self._audit_log_adapter.append_log(WorkerAuditLog(
                    worker_id=worker_id,
                    action=WorkerAuditAction.RUNTIME_STATE_CHANGED,
                    old_value=old_state.value if hasattr(old_state, 'value') else str(old_state),
                    new_value=WorkerRuntimeState.ONLINE.value,
                    source_type=WorkerSourceType.API,
                    performed_by=updated_by,
                ))
            except Exception as e:
                logger.warning(f"[RuntimeStateService] Failed to append audit log: {e}")
        else:
            logger.debug(f"[RuntimeStateService] No audit log adapter, skipping audit log")

        # 触发索引同步（通知检索层 Worker 状态变化）
        if self._index_sync_adapter:
            try:
                self._index_sync_adapter.on_runtime_state_changed(
                    worker_id=worker_id,
                    old_state=old_state,
                    new_state=WorkerRuntimeState.ONLINE,
                )
            except Exception as e:
                logger.warning(f"[RuntimeStateService] Failed to trigger index sync: {e}")
        else:
            logger.debug(f"[RuntimeStateService] No index sync adapter, skipping index sync")

        # 🔧 Worker 状态变化时重建向量索引，更新 payload 中的 availability/runtime_state
        # 向量从 ZDAS 加载（不重新 embed），只更新 payload 字段
        if self._vector_store:
            try:
                self._rebuild_worker_vectors(worker_id)
            except Exception as e:
                logger.warning(f"[RuntimeStateService] Failed to rebuild worker vectors: {e}")
        else:
            logger.debug(f"[RuntimeStateService] No vector store, skipping vector rebuild")

        logger.info(f"[RuntimeStateService] Worker {worker_id} set to ONLINE by {updated_by}")

        # Return fresh worker from store
        return self._registry_store.get_by_id(worker_id)

    def _rebuild_worker_vectors(self, worker_id: str) -> None:
        """
        重新构建 Worker 的向量索引

        用于 Worker 从 offline 状态上线时，重新生成向量索引以便被搜索到。

        Args:
            worker_id: Worker ID
        """
        try:
            # 使用 fusion_dependencies 中的函数构建索引
            from src.interfaces.api.dependencies.fusion_dependencies import _build_vector_index_for_worker
            success = _build_vector_index_for_worker(worker_id)
            if success:
                logger.info(f"[RuntimeStateService] Successfully rebuilt vector index for worker {worker_id}")
            else:
                logger.warning(f"[RuntimeStateService] Failed to rebuild vector index for worker {worker_id}")
        except Exception as e:
            logger.warning(f"[RuntimeStateService] Error rebuilding vector index for worker {worker_id}: {e}")

    def set_offline(
        self,
        worker_id: str,
        updated_by: Optional[str] = None,
    ) -> Worker:
        """
        设置 Worker 为离线状态

        规则：
        - 任何生命周期状态都可以设为 offline
        - 状态变化写审计日志
        - 状态变化触发 index sync
        - 删除该 worker 的所有向量数据（防止脏数据和召回率降低）

        Args:
            worker_id: Worker ID
            updated_by: 更新来源

        Returns:
            更新后的 Worker

        Raises:
            WorkerNotFoundException: Worker 不存在
        """
        # 获取 worker
        worker = self._registry_store.get_by_id(worker_id)
        if worker is None:
            raise WorkerNotFoundException(worker_id)

        # 获取当前 runtime state
        old_state = self._runtime_state_store.get_runtime_state(worker_id)
        if old_state is None:
            old_state = WorkerRuntimeState.OFFLINE

        # 如果已经是 offline，同步 worker.state 后返回
        if old_state == WorkerRuntimeState.OFFLINE:
            # 确保 worker.state.runtime_state 同步
            if isinstance(worker, dict):
                # OSS mode: dict worker
                # Already offline, just return
                return worker
            else:
                # Object mode: check and update runtime_state
                if worker.state.runtime_state != WorkerRuntimeState.OFFLINE:
                    worker.state.runtime_state = WorkerRuntimeState.OFFLINE
                    worker = self._registry_store.update(worker)
                return worker

        # 设置新状态
        self._runtime_state_store.set_runtime_state(
            worker_id=worker_id,
            runtime_state={
                "state": WorkerRuntimeState.OFFLINE.value,
                "heartbeat_at": None,
                "metadata": {}
            },
            updated_by=updated_by,
        )

        # Note: runtime_state is managed separately in runtime_state_store
        # No need to update worker object for OSS mode (dict)
        # Only update worker object if it's a Worker domain object
        if not isinstance(worker, dict):
            # Object mode: update Worker object
            worker.state.runtime_state = WorkerRuntimeState.OFFLINE
            try:
                worker = self._registry_store.update(worker)
            except Exception as e:
                logger.warning(f"[RuntimeStateService] Failed to update worker object: {e}")
                # Continue anyway - runtime state is already saved

        # Phase C1: Sync denormalized column workers.runtime_state
        # Try to call sync_runtime_state_mirror if registry_store has it
        try:
            if hasattr(self._registry_store, 'sync_runtime_state_mirror'):
                sync_success = self._registry_store.sync_runtime_state_mirror(
                    worker_id=worker_id,
                    runtime_state=WorkerRuntimeState.OFFLINE.value,
                )
                if sync_success:
                    logger.info(
                        f"[RuntimeStateService] Synced runtime_state mirror: "
                        f"worker_id={worker_id}, runtime_state=offline"
                    )
                else:
                    logger.warning(
                        f"[RuntimeStateService] Failed to sync runtime_state mirror: "
                        f"worker_id={worker_id}, runtime_state=offline"
                    )
            else:
                logger.debug(
                    f"[RuntimeStateService] registry_store does not support sync_runtime_state_mirror"
                )
        except Exception as e:
            logger.error(
                f"[RuntimeStateService] Error syncing runtime_state mirror: {e}",
                exc_info=True
            )

        # 记录审计日志
        if self._audit_log_adapter:
            try:
                self._audit_log_adapter.append_log(WorkerAuditLog(
                    worker_id=worker_id,
                    action=WorkerAuditAction.RUNTIME_STATE_CHANGED,
                    old_value=old_state.value if hasattr(old_state, 'value') else str(old_state),
                    new_value=WorkerRuntimeState.OFFLINE.value,
                    source_type=WorkerSourceType.API,
                    performed_by=updated_by,
                ))
            except Exception as e:
                logger.warning(f"[RuntimeStateService] Failed to append audit log: {e}")
        else:
            logger.debug(f"[RuntimeStateService] No audit log adapter, skipping audit log")

        # 触发索引同步（通知检索层 Worker 状态变化）
        if self._index_sync_adapter:
            try:
                self._index_sync_adapter.on_runtime_state_changed(
                    worker_id=worker_id,
                    old_state=old_state,
                    new_state=WorkerRuntimeState.OFFLINE,
                )
            except Exception as e:
                logger.warning(f"[RuntimeStateService] Failed to trigger index sync: {e}")
        else:
            logger.debug(f"[RuntimeStateService] No index sync adapter, skipping index sync")

        # 🔧 Worker 状态变化时重建向量索引，更新 payload 中的 availability/runtime_state
        # 向量从 ZDAS 加载（不重新 embed），只更新 payload 字段
        if self._vector_store:
            try:
                self._rebuild_worker_vectors(worker_id)
            except Exception as e:
                logger.warning(f"[RuntimeStateService] Failed to rebuild worker vectors: {e}")
        else:
            logger.debug(f"[RuntimeStateService] No vector store, skipping vector rebuild")

        logger.info(f"[RuntimeStateService] Worker {worker_id} set to OFFLINE by {updated_by}")

        # Return fresh worker from store
        return self._registry_store.get_by_id(worker_id)

    def set_availability(
        self,
        worker_id: str,
        availability: Availability,
        updated_by: Optional[str] = None,
    ) -> Worker:
        """
        设置 Worker 的可见性状态。

        Args:
            worker_id: Worker ID
            availability: 可见性状态 (private/protected/public)
            updated_by: 更新来源

        Returns:
            更新后的 Worker

        Raises:
            WorkerNotFoundException: Worker 不存在
        """
        # 获取 worker
        worker = self._registry_store.get_by_id(worker_id)
        if worker is None:
            raise WorkerNotFoundException(worker_id)

        # 获取旧的 availability
        old_availability = worker.state.availability

        # 如果已经是目标状态，直接返回
        if old_availability == availability:
            logger.info(
                f"[RuntimeStateService] Worker {worker_id} availability already set to {availability.value}"
            )
            return worker

        # 更新 Worker State 中的 availability 字段
        worker.state.availability = availability
        worker = self._registry_store.update(worker)

        # 更新底层 runtime_state_store（如果存在）
        try:
            self._runtime_state_store.set_availability(
                worker_id=worker_id,
                availability=availability,
                updated_by=updated_by,
            )
        except AttributeError:
            # 如果 runtime_state_store 没有 set_availability 方法，跳过
            pass

        # 记录审计日志
        if self._audit_log_adapter:
            try:
                from src.domain.models.worker_audit_log import WorkerAuditLog, WorkerAuditAction
                from src.domain.models.worker_source_info import WorkerSourceType
                self._audit_log_adapter.append_log(WorkerAuditLog(
                    worker_id=worker_id,
                    action=WorkerAuditAction.AVAILABILITY_CHANGED,
                    old_value=old_availability.value,
                    new_value=availability.value,
                    source_type=WorkerSourceType.API,
                    performed_by=updated_by,
                ))
            except Exception as e:
                logger.warning(f"[RuntimeStateService] Failed to append audit log: {e}")
        else:
            logger.debug(f"[RuntimeStateService] No audit log adapter, skipping audit log")

        logger.info(
            f"[RuntimeStateService] Worker {worker_id} availability changed from {old_availability.value} to {availability.value} by {updated_by}"
        )

        return worker

    def _delete_worker_vectors(self, worker_id: str) -> None:
        """
        删除指定 Worker 的所有向量数据

        通过 profile_key 前缀匹配删除该 worker 的所有向量：
        - 格式: {worker_id}:{profile_id}:{fragment_type}...
        - 匹配: {worker_id}:%

        Args:
            worker_id: Worker ID
        """
        if self._vector_store is None:
            logger.warning(f"[RuntimeStateService] Vector store not configured, skipping vector deletion for {worker_id}")
            return

        try:
            # 获取底层 vector store
            inner_store = self._vector_store.vector_store
            logger.debug(f"[RuntimeStateService] Vector store type: {type(inner_store).__name__}")

            # 获取所有向量 ID
            all_vector_ids = inner_store.get_vector_ids()
            logger.debug(f"[RuntimeStateService] Total vectors in store: {len(all_vector_ids)}")

            # 筛选以 worker_id: 开头的向量 ID
            prefix = f"{worker_id}:"
            worker_vector_ids = [
                vid for vid in all_vector_ids
                if vid.startswith(prefix)
            ]

            logger.debug(f"[RuntimeStateService] Found {len(worker_vector_ids)} vectors for worker {worker_id} (prefix: {prefix})")

            if worker_vector_ids:
                deleted_count = self._vector_store.delete_embeddings(worker_vector_ids)
                logger.info(f"[RuntimeStateService] Deleted {deleted_count} vectors for worker {worker_id}")
            else:
                logger.debug(f"[RuntimeStateService] No vectors found for worker {worker_id}")

        except Exception as e:
            # 向量删除失败不应影响主流程，记录错误即可
            logger.error(f"[RuntimeStateService] Failed to delete vectors for worker {worker_id}: {e}", exc_info=True)

    def get_runtime_state(self, worker_id: str) -> Optional[WorkerRuntimeState]:
        """
        获取 Worker 运行态

        Args:
            worker_id: Worker ID

        Returns:
            WorkerRuntimeState 或 None
        """
        return self._runtime_state_store.get_runtime_state(worker_id)

    def batch_get_runtime_states(
        self,
        worker_ids: list[str],
    ) -> dict[str, WorkerRuntimeState]:
        """
        批量获取运行态

        Args:
            worker_ids: Worker ID 列表

        Returns:
            dict[worker_id, WorkerRuntimeState]
        """
        return self._runtime_state_store.batch_get_runtime_states(worker_ids)


__all__ = ["WorkerRuntimeStateService"]