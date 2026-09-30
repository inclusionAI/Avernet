"""
Registry Worker Profile Source

从 Worker Registry 直接构建 WorkerProfile。

解决问题：
- Worker 注册写入 bcsfuse_workers 表
- 原 ApiWorkerProfileSource 读取 worker_profile_contents 表
- 两者没有关联，导致 scan() 返回 0 profiles

解决方案：
- 直接从 Worker Registry 读取已注册的 Workers
- 构建 WorkerProfile 供检索使用

优先级：高于 API Source 和 FILE Source
"""

from __future__ import annotations

import logging
from typing import Optional

from src.domain.models.context_fragment import ContextFragment, ContextKind
from src.domain.models.skill_profile import SkillProfile
from src.domain.models.worker_profile import (
    WorkerProfile,
    WorkerProfileScanResult,
    ProfileType,
    SourceType,
)
from src.domain.models.worker import Worker, WorkerType
from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
from src.domain.models.worker_runtime_state import WorkerRuntimeState
from src.domain.services.worker_profile_source import WorkerProfileSource
from src.domain.services.adapters.worker_registry_store_adapter import (
    WorkerRegistryStoreAdapter,
)
from src.domain.services.adapters.worker_runtime_state_store_adapter import (
    WorkerRuntimeStateStoreAdapter,
)


logger = logging.getLogger(__name__)


class RegistryWorkerProfileSource:
    """
    从 Worker Registry 构建 WorkerProfile

    与 ApiWorkerProfileSource/FileWorkerProfileSource 并行工作：
    - RegistryWorkerProfileSource: 从已注册 Worker 构建 (优先级最高)
    - ApiWorkerProfileSource: 从 API 注册的 Profile Content 加载
    - FileWorkerProfileSource: 从文件系统加载

    只返回 lifecycle_state=active 且 runtime_state=online 的 Worker。
    """

    def __init__(
        self,
        registry_store: WorkerRegistryStoreAdapter,
        runtime_state_store: Optional[WorkerRuntimeStateStoreAdapter] = None,
        include_offline: bool = False,
    ):
        """
        初始化

        Args:
            registry_store: Worker Registry Store
            runtime_state_store: Runtime State Store (可选，用于状态过滤)
            include_offline: 是否包含 offline 状态的 Worker (默认 False)
        """
        self._registry_store = registry_store
        self._runtime_state_store = runtime_state_store
        self._include_offline = include_offline

    def scan(self) -> WorkerProfileScanResult:
        """
        扫描所有活跃 Worker 并构建 Profile

        Returns:
            WorkerProfileScanResult
        """
        profiles = []
        scan_warnings = []

        try:
            # 获取所有 active 状态的 Worker（使用 list 方法，传入 lifecycle_states 过滤）
            all_workers = self._registry_store.list(
                lifecycle_states=[WorkerLifecycleState.ACTIVE]
            )

            for worker in all_workers:
                # 已在 list() 中过滤 lifecycle_state，这里只需检查 runtime_state

                # 过滤：检查 runtime state (如果提供了 store)
                if self._runtime_state_store and not self._include_offline:
                    try:
                        # 使用正确的接口方法 get_runtime_state()
                        runtime_state = self._runtime_state_store.get_runtime_state(worker.id)
                        # 接口返回 Optional[WorkerRuntimeState]，直接是枚举值，不需要 .state
                        if runtime_state and runtime_state != WorkerRuntimeState.ONLINE:
                            continue
                    except Exception as e:
                        logger.debug(f"Failed to get runtime state for {worker.id}: {e}")
                        # 无法获取状态时，默认包含（根据 include_offline 配置）
                        if not self._include_offline:
                            continue

                # 转换为 WorkerProfile
                profile = self._convert_to_worker_profile(worker)
                if profile:
                    profiles.append(profile)

            logger.info(
                f"[RegistryProfileSource] scan() 返回 {len(profiles)} profiles "
                f"(total active workers: {len(all_workers)})"
            )

        except Exception as e:
            logger.error(f"[RegistryProfileSource] scan() failed: {e}")
            scan_warnings.append({
                "code": "REGISTRY_SOURCE_ERROR",
                "message": str(e),
            })

        return WorkerProfileScanResult(
            profiles=profiles,
            scan_warnings=scan_warnings,
            source_roots=["registry"],
        )

    def get_profile(
        self, staff_id: str, profile_id: str
    ) -> Optional[WorkerProfile]:
        """
        获取指定 Profile

        Args:
            staff_id: 员工 ID (对应 worker_id)
            profile_id: Profile ID (对于 Registry Source，总是 "default")

        Returns:
            WorkerProfile 或 None
        """
        try:
            worker = self._registry_store.get_by_id(staff_id)
            if worker is None:
                return None

            # 检查状态
            if worker.lifecycle_state != WorkerLifecycleState.ACTIVE:
                return None

            return self._convert_to_worker_profile(worker)

        except Exception as e:
            logger.debug(f"Failed to get profile for {staff_id}: {e}")
            return None

    def get_profiles_by_staff(self, staff_id: str) -> list[WorkerProfile]:
        """
        获取指定员工的所有 Profile

        对于 Registry Source，每个 Worker 只有一个默认 Profile。

        Args:
            staff_id: 员工 ID (对应 worker_id)

        Returns:
            WorkerProfile 列表 (最多一个)
        """
        profile = self.get_profile(staff_id, "default")
        return [profile] if profile else []

    def _convert_to_worker_profile(
        self, worker: Worker
    ) -> Optional[WorkerProfile]:
        """
        将 Worker 转换为 WorkerProfile

        Args:
            worker: Worker 实例

        Returns:
            WorkerProfile 或 None（如果 Worker 没有实质内容）
        """
        if worker is None:
            return None

        # 确定 profile_id：优先使用 worker.active_profile_key 中的 profile_id
        profile_id = (
            worker.active_profile_key.split(":")[1]
            if worker.active_profile_key and ":" in worker.active_profile_key
            else "default"
        )

        # 检查 Worker 是否有实质内容
        # 如果没有 identity、responsibilities、skills、capabilities，则视为空 profile
        has_content = (
            (worker.identity and worker.identity.description) or
            worker.responsibilities or
            worker.skills or
            worker.capabilities or
            worker.domains
        )

        if not has_content:
            logger.debug(
                f"[RegistryProfileSource] Skipping worker {worker.id} - no profile content"
            )
            return None

        # Phase E4 Fix: 不应该跳过 API 注册的 worker
        # 原始逻辑错误：即使 worker 有 API-registered active_profile_key，
        # RegistryWorkerProfileSource 也应该返回这个 worker 的基础信息
        # APIProfileSource 会负责加载完整的 profile content
        #
        # 修复说明：
        # - CompositeWorkerProfileSource 会合并 Registry + API 的信息
        # - Registry source 提供基础的 worker 信息(identity, skills, domains)
        # - API source 提供完整的 profile content(SOUL.md, AGENTS.md)
        # - 两者应该互补,而不是互斥
        #
        # 移除错误的跳过逻辑
        # if worker.active_profile_key and worker.active_profile_key.startswith(f"{worker.id}:"):
        #     logger.debug(...)
        #     return None

        # 构建 Context Fragments
        context_fragments = []
        registry_source = f"registry://{worker.id}/{profile_id}"

        # 从 identity.description 构建 SOUL fragment
        worker_name = worker.identity.name if worker.identity else worker.id
        worker_description = worker.identity.description if worker.identity else None

        if worker_description:
            soul_content = f"# {worker_name}\n\n{worker_description}"
            context_fragments.append(ContextFragment(
                kind=ContextKind.SOUL,
                filename="SOUL.md",
                content=soul_content,
                source_path=registry_source,
            ))

        # 从 responsibilities 构建 AGENTS fragment
        if worker.responsibilities:
            agents_content = f"# Responsibilities\n\n"
            for resp in worker.responsibilities:
                agents_content += f"- {resp}\n"
            context_fragments.append(ContextFragment(
                kind=ContextKind.AGENT,
                filename="AGENTS.md",
                content=agents_content,
                source_path=registry_source,
            ))

        # 构建 Skills - worker.skills 是 list[SkillRef]
        active_skills = []
        for idx, skill in enumerate(worker.skills or []):
            # skill 是 SkillRef 对象
            active_skills.append(SkillProfile(
                name=skill.name,
                description=skill.description or "",
                skill_id=f"registry_skill_{worker.id}_{idx}",
                path=None,
                skill_set_name="registry_skills",
                is_active=True,
                metadata={"source": skill.source.value, "trust_level": skill.trust_level.value},
            ))

        # 从 capabilities 构建技能 - worker.capabilities 是 list[Capability]
        for idx, cap in enumerate(worker.capabilities or []):
            # cap 是 Capability 对象
            if cap.name:
                active_skills.append(SkillProfile(
                    name=cap.name,
                    description=f"Capability: {cap.name}",
                    skill_id=f"registry_cap_{worker.id}_{idx}",
                    path=None,
                    skill_set_name="registry_capabilities",
                    is_active=True,
                    metadata={"level": cap.level.value if hasattr(cap.level, 'value') else str(cap.level)},
                ))

        # 创建 WorkerProfile
        # ProfileType: BOT -> BOT, HUMAN -> DEFAULT (人类员工的默认数字分身)
        profile_type = ProfileType.BOT if worker.type == WorkerType.BOT else ProfileType.DEFAULT

        profile = WorkerProfile(
            staff_id=worker.id,
            profile_id=profile_id,
            profile_type=profile_type,
            source_type=SourceType.API,  # API 注册的 Worker
            source_root="registry",
            context_fragments=context_fragments,
            active_skills=active_skills,
        )

        # 生成可检索文本
        profile.generate_searchable_text()

        # 追加额外信息
        extra_parts = []

        if worker.domains:
            extra_parts.append(f"Domains: {', '.join(worker.domains)}")

        if worker.responsibilities:
            extra_parts.append(f"Responsibilities: {', '.join(worker.responsibilities)}")

        if worker.capabilities:
            # capabilities 是 list[Capability]
            cap_names = [cap.name for cap in worker.capabilities if cap.name]
            extra_parts.append(f"Capabilities: {', '.join(cap_names)}")

        if extra_parts:
            profile.searchable_text = f"{profile.searchable_text} {' '.join(extra_parts)}"

        return profile


__all__ = ["RegistryWorkerProfileSource"]