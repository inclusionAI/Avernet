"""
Composite Worker Profile Source

Profile API MVP Phase 2 - 合并 Registry + API + FILE 三类 Profile 来源

合并策略 (G5 稀疏上下文修复):
1. Registry profile 作为基础（提供 worker 信息）
2. API profile 内容合并到 Registry profile（补充 context_fragments, active_skills）
3. FILE profile 作为补充（仅当 Registry + API 都没有时）

修复问题：
- 解决 Worker 注册后 profile 只有最少的 fragments/skills 问题
- 当 Registry profile 是稀疏的，从 API profile 补充内容
"""

from __future__ import annotations

import logging
from typing import Optional

from src.domain.models.worker_profile import (
    WorkerProfile,
    WorkerProfileScanResult,
    WorkerProfileWarning,
)
from src.domain.services.worker_profile_source import WorkerProfileSource


logger = logging.getLogger(__name__)


class CompositeWorkerProfileSource:
    """
    组合 Worker Profile 来源

    合并 Registry、API 和文件系统三类 Profile 来源。

    合并策略 (G5 稀疏上下文修复):
    1. Registry profile 作为基础（提供 worker 信息）
    2. 如果 Registry profile 没有足够的 context_fragments 或 active_skills，
       从 API profile 合并补充
    3. FILE profile 作为补充（仅当 Registry + API 都没有时）

    设计决策：
    - 解决 Worker 注册后 profile 只有最少的 fragments/skills 问题
    - 当 Registry profile 是稀疏的，从 API profile 补充内容
    - 保持 Registry 的 worker 基础信息不变
    """

    def __init__(
        self,
        api_source: Optional[WorkerProfileSource] = None,
        file_source: Optional[WorkerProfileSource] = None,
        registry_source: Optional[WorkerProfileSource] = None,
    ):
        """
        初始化组合来源

        Args:
            api_source: API 注册的 Profile 来源
            file_source: 文件系统的 Profile 来源
            registry_source: 从 Worker Registry 构建的 Profile 来源（最高优先级）
        """
        self._api_source = api_source
        self._file_source = file_source
        self._registry_source = registry_source

        # Profile 缓存
        self._scan_result: Optional[WorkerProfileScanResult] = None

    def set_api_source(self, source: WorkerProfileSource) -> None:
        """设置 API 来源"""
        self._api_source = source
        self._clear_cache()

    def set_file_source(self, source: WorkerProfileSource) -> None:
        """设置文件来源"""
        self._file_source = source
        self._clear_cache()

    def set_registry_source(self, source: WorkerProfileSource) -> None:
        """设置 Registry 来源"""
        self._registry_source = source
        self._clear_cache()

    def _clear_cache(self) -> None:
        """清除缓存"""
        self._scan_result = None

    def scan(self) -> WorkerProfileScanResult:
        """
        扫描并合并所有 Profile

        Merged Profile 策略 (G5 稀疏上下文修复):
        1. Registry profile 作为基础（提供 worker 信息）
        2. API profile 内容合并到 Registry profile（补充 context_fragments, active_skills）
        3. FILE profile 作为补充（仅当 Registry + API 都没有时）

        注意：如果 API Source 未设置，会自动创建一个实例

        Phase G6H Fix: Remove composite scan cache to ensure fresh data for newly created workers.
        This matches the behavior where APIProfileSource now performs fresh DB queries on every scan.
        Cache removal ensures profile retrieval sees workers created after initialization.

        Returns:
            WorkerProfileScanResult
        """
        # Phase G6H Fix: Clear cache before every scan to ensure fresh data
        self._clear_cache()

        # 自动创建 API Profile Source（如果没有）
        if self._api_source is None:
            try:
                from src.infra.worker_profiles.sources.api_profile_source import APIProfileSource
                self._api_source = APIProfileSource()
                logger.info("[CompositeSource] Auto-created APIProfileSource for sparse context fix")
            except Exception as e:
                logger.warning(f"[CompositeSource] Failed to create APIProfileSource: {e}")

        profiles = []
        scan_warnings = []
        source_roots = []

        # 收集 profile_keys 和 profiles 的映射 (用于合并)
        profile_map: dict[str, "WorkerProfile"] = {}
        api_profiles: dict[str, "WorkerProfile"] = {}
        file_profiles: dict[str, "WorkerProfile"] = {}

        # 1. 加载 Registry 来源（从已注册 Worker 构建）
        if self._registry_source:
            try:
                registry_result = self._registry_source.scan()
                for profile in registry_result.profiles:
                    profile_key = profile.profile_key
                    profile_map[profile_key] = profile
                    logger.debug(f"Added Registry profile: {profile_key}")
                scan_warnings.extend(registry_result.scan_warnings)
                source_roots.extend(registry_result.source_roots)
            except Exception as e:
                logger.warning(f"Failed to scan Registry source: {e}")
                scan_warnings.append(WorkerProfileWarning(
                    code="REGISTRY_SOURCE_ERROR",
                    message=str(e),
                    source_path=None,
                    suggestion=None,
                ))

        # 2. 加载 API 来源的活跃 Profile (用于合并)
        if self._api_source:
            try:
                api_result = self._api_source.scan()
                for profile in api_result.profiles:
                    profile_key = profile.profile_key
                    api_profiles[profile_key] = profile
                    logger.debug(f"Loaded API profile for merge: {profile_key}")
                scan_warnings.extend(api_result.scan_warnings)
                source_roots.extend(api_result.source_roots)
            except Exception as e:
                logger.warning(f"Failed to scan API source: {e}")
                scan_warnings.append(WorkerProfileWarning(
                    code="API_SOURCE_ERROR",
                    message=str(e),
                    source_path=None,
                    suggestion=None,
                ))

        # 3. 加载 FILE 来源 (用于补充)
        if self._file_source:
            try:
                file_result = self._file_source.scan()
                for profile in file_result.profiles:
                    profile_key = profile.profile_key
                    file_profiles[profile_key] = profile
                    logger.debug(f"Loaded FILE profile for supplement: {profile_key}")
                scan_warnings.extend(file_result.scan_warnings)
                source_roots.extend(file_result.source_roots)
            except Exception as e:
                logger.warning(f"Failed to scan FILE source: {e}")
                scan_warnings.append(WorkerProfileWarning(
                    code="FILE_SOURCE_ERROR",
                    message=str(e),
                    source_path=None,
                    suggestion=None,
                ))

        # 4. 合并策略：Registry + API -> Merged Profile
        # 使用 staff_id 进行匹配
        for profile_key, registry_profile in profile_map.items():
            merged_profile = registry_profile

            # 尝试直接匹配 profile_key
            if profile_key in api_profiles:
                api_profile = api_profiles[profile_key]
                merged_profile = self._merge_profiles(registry_profile, api_profile)
                logger.info(
                    f"Merged Registry+API profile: {profile_key}, "
                    f"fragments={len(merged_profile.context_fragments or [])}, "
                    f"skills={len(merged_profile.active_skills or [])}"
                )
            else:
                # 尝试通过 staff_id 匹配
                matched_api_profile = self._find_matching_api_profile(
                    registry_profile, api_profiles
                )
                if matched_api_profile:
                    merged_profile = self._merge_profiles(registry_profile, matched_api_profile)
                    logger.info(
                        f"Merged Registry+API profile by staff_id: {profile_key} <-> {matched_api_profile.profile_key}, "
                        f"fragments={len(merged_profile.context_fragments or [])}, "
                        f"skills={len(merged_profile.active_skills or [])}"
                    )

            profiles.append(merged_profile)

        # 5. 添加只有 API 来源的 Profile
        for profile_key, api_profile in api_profiles.items():
            if profile_key not in profile_map:
                profiles.append(api_profile)
                logger.debug(f"Added API-only profile: {profile_key}")

        # 6. 添加只有 FILE 来源的 Profile
        for profile_key, file_profile in file_profiles.items():
            if profile_key not in profile_map and profile_key not in api_profiles:
                profiles.append(file_profile)
                logger.debug(f"Added FILE-only profile: {profile_key}")

        logger.info(
            f"Composite source scan complete: {len(profiles)} profiles "
            f"(Registry merged with API + FILE supplemented)"
        )

        # Phase G6H Fix: Return result directly without caching
        # This ensures newly created workers are visible in subsequent scans
        return WorkerProfileScanResult(
            profiles=profiles,
            scan_warnings=scan_warnings,
            source_roots=source_roots,
        )

    def _find_matching_api_profile(
        self,
        registry_profile: "WorkerProfile",
        api_profiles: dict[str, "WorkerProfile"],
    ) -> Optional["WorkerProfile"]:
        """
        通过 staff_id 查找匹配的 API Profile

        Args:
            registry_profile: Registry 来源的 Profile
            api_profiles: API profiles 的字典

        Returns:
            匹配的 API Profile 或 None
        """
        registry_staff_id = registry_profile.staff_id

        # 规范化 staff_id
        normalized_registry_id = self._normalize_staff_id(registry_staff_id)

        for api_key, api_profile in api_profiles.items():
            api_staff_id = api_profile.staff_id
            normalized_api_id = self._normalize_staff_id(api_staff_id)

            if normalized_registry_id == normalized_api_id:
                logger.debug(
                    f"Matched profiles by staff_id: registry={registry_staff_id}, api={api_staff_id}"
                )
                return api_profile

        return None

    def _normalize_staff_id(self, staff_id: str) -> str:
        """
        规范化 staff_id（移除常见的前缀）

        Args:
            staff_id: 原始 staff_id

        Returns:
            规范化后的 staff_id
        """
        # 移除常见前缀
        prefixes_to_remove = ["bot_"]
        normalized = staff_id
        for prefix in prefixes_to_remove:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]
                break
        return normalized

    def _merge_profiles(
        self,
        registry_profile: "WorkerProfile",
        api_profile: "WorkerProfile",
    ) -> "WorkerProfile":
        """
        合并 Registry 和 API Profile

        G5 Sparse Context 修复：
        - 策略变更：当 API profile 内容更丰富时，总是合并
        - Registry profile 来自 Worker 注册，内容可能很简略（只有 responsibilities/capabilities）
        - API profile 来自 soul_md 上传，内容丰富（完整的专家画像）
        - 应该优先使用更丰富的内容，而不是只在 Registry 为空时才合并

        新合并策略：
        1. 如果 API profile 有更多 fragments 或更多 skills，则合并
        2. 合并时优先使用 API 的丰富内容
        3. 保留 Registry 的基础信息

        Args:
            registry_profile: Registry 来源的 Profile
            api_profile: API 来源的 Profile

        Returns:
            合并后的 Profile
        """
        from src.domain.models.worker_profile import WorkerProfile

        # 判断内容丰富度
        registry_fragments = registry_profile.context_fragments or []
        registry_skills = registry_profile.active_skills or []
        api_fragments = api_profile.context_fragments or []
        api_skills = api_profile.active_skills or []

        # 计算内容长度（用于比较丰富度）
        registry_fragment_content = sum(len(f.content or "") for f in registry_fragments)
        api_fragment_content = sum(len(f.content or "") for f in api_fragments)

        # 判断是否需要合并：
        # 1. Registry 完全空 → 合并
        # 2. API 内容更丰富（更多 fragment 内容 或 更多 skills）→ 合并
        is_registry_sparse = len(registry_fragments) == 0 and len(registry_skills) == 0
        api_has_richer_content = (
            api_fragment_content > registry_fragment_content or
            len(api_skills) > len(registry_skills)
        )

        if not is_registry_sparse and not api_has_richer_content:
            # Registry profile 已经足够丰富，且 API 没有提供更好的内容
            logger.debug(
                f"Registry profile has sufficient content, skip merge: {registry_profile.profile_key}"
            )
            return registry_profile

        # 如果 API 也没有内容，返回原 registry profile
        if len(api_fragments) == 0 and len(api_skills) == 0:
            logger.debug(f"No API content to merge for {registry_profile.profile_key}")
            return registry_profile

        # 合并策略：
        # - 如果 API 的 fragment 内容 > Registry，使用 API 的 fragments
        # - 否则保留 Registry 的 fragments
        use_api_fragments = api_fragment_content > registry_fragment_content
        use_api_skills = len(api_skills) > len(registry_skills)

        final_fragments = api_fragments if use_api_fragments else registry_fragments
        final_skills = api_skills if use_api_skills else registry_skills

        # 创建合并后的 Profile
        merged_profile = WorkerProfile(
            staff_id=registry_profile.staff_id,
            profile_id=registry_profile.profile_id,
            profile_type=registry_profile.profile_type,
            source_type=registry_profile.source_type,
            source_root=registry_profile.source_root,
            # 合并 context_fragments：优先使用内容更丰富的来源
            context_fragments=final_fragments,
            # 合并 active_skills：优先使用数量更多的来源
            active_skills=final_skills,
            # warnings 合并（如果有）
            warnings=list(registry_profile.warnings or []) + list(api_profile.warnings or []),
        )

        # 生成 searchable_text
        merged_profile.generate_searchable_text()

        # 追加 API profile 的额外信息
        if api_profile.searchable_text:
            merged_profile.searchable_text = f"{merged_profile.searchable_text} {api_profile.searchable_text}"

        merge_reason = "sparse" if is_registry_sparse else "richer_api_content"
        logger.info(
            f"Profile merge complete ({merge_reason}): {registry_profile.profile_key}, "
            f"fragments={len(final_fragments)} (api={use_api_fragments}), "
            f"skills={len(final_skills)} (api={use_api_skills})"
        )

        return merged_profile

    def get_profile(
        self, staff_id: str, profile_id: str
    ) -> Optional[WorkerProfile]:
        """
        获取指定 Profile

        使用合并策略：
        1. 从 Registry 获取基础 profile
        2. 从 API 获取内容 profile
        3. 如果 Registry profile 是稀疏的，合并 API 内容

        Args:
            staff_id: 员工 ID
            profile_id: Profile ID

        Returns:
            WorkerProfile 或 None
        """
        registry_profile = None
        api_profile = None
        file_profile = None

        # 1. 从 Registry 获取
        if self._registry_source:
            registry_profile = self._registry_source.get_profile(staff_id, profile_id)

        # 2. 从 API 获取
        if self._api_source:
            api_profile = self._api_source.get_profile(staff_id, profile_id)

        # 3. 从 FILE 获取
        if self._file_source:
            file_profile = self._file_source.get_profile(staff_id, profile_id)

        # 4. 合并策略
        if registry_profile:
            if api_profile:
                # 尝试合并
                merged = self._merge_profiles(registry_profile, api_profile)
                if merged is not registry_profile:
                    logger.info(
                        f"get_profile: merged Registry+API for {staff_id}:{profile_id}"
                    )
                return merged
            return registry_profile

        if api_profile:
            return api_profile

        return file_profile

    def get_profiles_by_staff(self, staff_id: str) -> list[WorkerProfile]:
        """
        获取指定员工的所有 Profile

        合并 Registry、API 和 FILE 来源，去重。

        Args:
            staff_id: 员工 ID

        Returns:
            WorkerProfile 列表
        """
        profiles = []
        seen_ids: set[str] = set()

        # 0. 最高优先级：从 Registry 获取
        if self._registry_source:
            for profile in self._registry_source.get_profiles_by_staff(staff_id):
                key = profile.profile_key
                if key not in seen_ids:
                    profiles.append(profile)
                    seen_ids.add(key)

        # 1. 次高优先级：从 API 获取
        if self._api_source:
            for profile in self._api_source.get_profiles_by_staff(staff_id):
                key = profile.profile_key
                if key not in seen_ids:
                    profiles.append(profile)
                    seen_ids.add(key)

        # 2. 从 FILE 获取（去重）
        if self._file_source:
            for profile in self._file_source.get_profiles_by_staff(staff_id):
                key = profile.profile_key
                if key not in seen_ids:
                    profiles.append(profile)
                    seen_ids.add(key)

        return profiles


__all__ = ["CompositeWorkerProfileSource"]