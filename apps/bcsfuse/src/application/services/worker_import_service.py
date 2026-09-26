"""
Worker Import Service

Worker 导入应用服务。

Stage 1 职责：
- import_from_api: API 注册（主路径）
- import_from_file_profile: 文件导入（兼容路径）

规则：
- API 优先于 FILE
- FILE 导入不能覆盖显式管理字段
- 能创建 worker 或补充 profile 信息
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from src.domain.models.worker import Worker
from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
from src.domain.models.worker_runtime_state import WorkerRuntimeState
from src.domain.models.worker_audit_log import WorkerAuditLog, WorkerAuditAction
from src.domain.models.worker_source_info import WorkerSourceType
from src.domain.models.worker_profile import WorkerProfile
from src.domain.models.worker_profile_binding import WorkerProfileBinding
from src.domain.services.adapters.worker_registry_store_adapter import (
    WorkerRegistryStoreAdapter,
)
from src.domain.services.adapters.worker_runtime_state_store_adapter import (
    WorkerRuntimeStateStoreAdapter,
)
from src.domain.services.adapters.worker_profile_binding_store_adapter import (
    WorkerProfileBindingStoreAdapter,
)
from src.domain.services.adapters.worker_audit_log_adapter import (
    WorkerAuditLogAdapter,
)
from src.domain.services.adapters.worker_index_sync_adapter import (
    WorkerIndexSyncAdapter,
)
from src.domain.exceptions import DuplicateWorkerException


logger = logging.getLogger(__name__)


class WorkerImportService:
    """
    Worker 导入服务

    Stage 1 职责：
    - 统一导入入口（API / FILE）
    - 冲突解决
    - 审计日志
    """

    def __init__(
        self,
        registry_store: WorkerRegistryStoreAdapter,
        runtime_state_store: WorkerRuntimeStateStoreAdapter,
        profile_binding_store: WorkerProfileBindingStoreAdapter,
        audit_log_adapter: WorkerAuditLogAdapter,
        index_sync_adapter: WorkerIndexSyncAdapter,
    ):
        """
        初始化服务

        Args:
            registry_store: Worker Registry Store
            runtime_state_store: Runtime State Store
            profile_binding_store: Profile Binding Store
            audit_log_adapter: Audit Log Adapter
            index_sync_adapter: Index Sync Adapter
        """
        self._registry_store = registry_store
        self._runtime_state_store = runtime_state_store
        self._profile_binding_store = profile_binding_store
        self._audit_log_adapter = audit_log_adapter
        self._index_sync_adapter = index_sync_adapter

    def import_from_api(
        self,
        worker_data: dict[str, Any],
        actor: Optional[str] = None,
    ) -> Worker:
        """
        从 API 注册 Worker

        这是未来主路径。

        规则：
        - 如果 worker 已存在（by worker_id），返回冲突错误
        - 设置 source_type = API
        - 设置 lifecycle_state = ACTIVE
        - 初始化 runtime_state = OFFLINE
        - 记录审计日志
        - 触发索引同步

        Args:
            worker_data: Worker 数据
            actor: 操作者

        Returns:
            创建的 Worker

        Raises:
            DuplicateWorkerException: Worker 已存在
            ValueError: 数据校验失败
        """
        # 设置来源和初始状态
        worker_data["source_type"] = WorkerSourceType.API.value
        if "lifecycle_state" not in worker_data:
            worker_data["lifecycle_state"] = WorkerLifecycleState.ACTIVE.value

        # 创建 Worker
        worker = Worker.model_validate(worker_data)

        # 检查是否已存在
        if self._registry_store.exists(worker.id):
            raise DuplicateWorkerException(worker.id)

        # 持久化
        created = self._registry_store.create(worker)

        # 初始化 runtime state
        self._runtime_state_store.set_runtime_state(
            worker_id=created.id,
            runtime_state=WorkerRuntimeState.OFFLINE,
            updated_by="system",
        )

        # 如果提供了 active_profile_key，创建 Profile Binding
        if created.active_profile_key:
            self._profile_binding_store.bind_profile(
                worker_id=created.id,
                profile_key=created.active_profile_key,
                source_type=WorkerSourceType.API,
            )
            self._profile_binding_store.set_active_profile(
                worker_id=created.id,
                profile_key=created.active_profile_key,
            )
            logger.info(f"Created profile binding for worker {created.id} -> {created.active_profile_key}")

        # 记录审计日志
        self._audit_log_adapter.append_log(WorkerAuditLog(
            worker_id=created.id,
            action=WorkerAuditAction.CREATED,
            new_value=created.model_dump_json(),
            source_type=WorkerSourceType.API,
            performed_by=actor,
        ))

        # 触发索引同步
        self._index_sync_adapter.on_worker_created(created)

        logger.info(f"Worker {created.id} imported from API by {actor}")

        return created

    def import_from_file_profile(
        self,
        profile: WorkerProfile,
        worker_id: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> Worker:
        """
        从文件 Profile 导入 Worker

        这是兼容路径。

        规则：
        - 如果 worker 已存在（by external_id = profile_key）：
          - API 注册优先，FILE 导入不覆盖显式管理字段
          - 可以补充 profile 信息
        - 如果 worker 不存在：
          - 创建新 Worker
          - 设置 source_type = FILE
          - 设置 lifecycle_state = ACTIVE
          - 初始化 runtime_state = OFFLINE

        Args:
            profile: Worker Profile（从文件扫描得到）
            worker_id: 可选的 Worker ID（如果不提供，会生成）
            actor: 操作者

        Returns:
            创建或更新的 Worker
        """
        # 生成 external_id（从 profile_key）
        external_id = profile.profile_key

        # 检查是否已存在（by external_id）
        existing_workers = self._registry_store.list(
            # 注意：这里简化处理，实际应该有 external_id 过滤
            # Stage 1 先用内存实现，遍历查找
        )

        existing = None
        for w in existing_workers:
            if w.external_id == external_id:
                existing = w
                break

        if existing is not None:
            # 已存在：FILE 导入不覆盖显式管理字段
            # 但可以补充 profile 信息
            logger.info(
                f"Worker already exists for profile {external_id}, "
                f"skipping FILE import (API priority rule)"
            )
            return existing

        # 不存在：创建新 Worker
        # 从 profile 提取信息
        worker_data = self._profile_to_worker_data(
            profile=profile,
            worker_id=worker_id,
        )

        # 设置来源和初始状态
        worker_data["source_type"] = WorkerSourceType.FILE.value
        worker_data["source_ref"] = profile.source_root
        worker_data["external_id"] = external_id
        worker_data["lifecycle_state"] = WorkerLifecycleState.ACTIVE.value

        # 创建 Worker
        worker = Worker.model_validate(worker_data)

        # 持久化
        created = self._registry_store.create(worker)

        # 初始化 runtime state
        self._runtime_state_store.set_runtime_state(
            worker_id=created.id,
            runtime_state=WorkerRuntimeState.OFFLINE,
            updated_by="system",
        )

        # 绑定 Profile
        self._profile_binding_store.bind_profile(
            worker_id=created.id,
            profile_key=profile.profile_key,
            source_type=WorkerSourceType.FILE,
        )

        # 更新 Worker 的 active_profile_key
        created.active_profile_key = profile.profile_key
        created = self._registry_store.update(created)

        # 记录审计日志
        self._audit_log_adapter.append_log(WorkerAuditLog(
            worker_id=created.id,
            action=WorkerAuditAction.IMPORTED,
            new_value=created.model_dump_json(),
            source_type=WorkerSourceType.FILE,
            source_ref=profile.source_root,
            performed_by=actor,
        ))

        # 触发索引同步
        self._index_sync_adapter.on_worker_created(created)

        logger.info(
            f"Worker {created.id} imported from FILE "
            f"(profile: {profile.profile_key}) by {actor}"
        )

        return created

    def _profile_to_worker_data(
        self,
        profile: WorkerProfile,
        worker_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        将 Profile 转换为 Worker 数据

        Stage 1 简化实现：
        - 从 profile 中提取基本信息
        - 生成默认值

        Args:
            profile: Worker Profile
            worker_id: 可选的 Worker ID

        Returns:
            Worker 数据字典
        """
        import uuid

        # 生成 worker_id
        if worker_id is None:
            worker_id = f"wrk_{uuid.uuid4().hex[:12]}"

        # 确定类型（从 profile_type 推断）
        from src.domain.models.worker import WorkerType
        worker_type = (
            WorkerType.BOT if profile.profile_type.value == "bot"
            else WorkerType.HUMAN
        )

        # 提取名称
        name = profile.staff_id
        if profile.context_fragments:
            # 尝试从 SOUL.md 或其他文件中提取名称
            for fragment in profile.context_fragments:
                content = fragment.content or ""
                if "name:" in content.lower():
                    # 简单提取
                    lines = content.split("\n")
                    for line in lines:
                        if "name:" in line.lower():
                            parts = line.split(":", 1)
                            if len(parts) > 1:
                                name = parts[1].strip()
                                break
                    break

        # 构建数据
        return {
            "id": worker_id,
            "type": worker_type.value,
            "identity": {
                "name": name,
                "handle": f"@{profile.staff_id}",
                "description": f"Worker from profile {profile.profile_key}",
            },
            "responsibilities": ["general"],
            "domains": [],
            "capabilities": [
                {
                    "name": skill.name,
                    "level": "intermediate",
                }
                for skill in profile.active_skills[:3]  # 最多 3 个
            ],
            "skills": [],
            "resources": [],
            "state": {
                "availability": "available",
                "trust_level": "guarded",
            },
            # 从 profile 提取的领域（如果有）
            # Stage 1 简化为空
            "domains": [],
        }


__all__ = ["WorkerImportService"]