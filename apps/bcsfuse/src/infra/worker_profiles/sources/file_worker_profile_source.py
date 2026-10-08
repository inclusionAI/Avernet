"""
File Worker Profile Source

Worker Profile Ingestion Baseline

文件系统实现的 Worker Profile 来源。
"""

from __future__ import annotations

from typing import Optional

from src.domain.models.worker_profile import (
    WorkerProfile,
    WorkerProfileScanResult,
    ProfileType,
    SourceType,
)
from src.domain.services.worker_profile_source import WorkerProfileSource
from src.infra.worker_profiles.config.worker_profile_settings import WorkerProfileSettings
from src.infra.worker_profiles.scanners.file_scanner import FileScanner, ScanEntry
from src.infra.worker_profiles.loaders.file_context_loader import FileContextLoader
from src.infra.worker_profiles.loaders.file_skill_loader import FileSkillLoader


class FileWorkerProfileSource:
    """
    文件系统 Worker Profile 来源

    协调 Scanner、ContextLoader、SkillLoader 完成以下工作：
    1. 扫描目录结构识别有效 profile 目录
    2. 加载上下文文件（md）
    3. 加载技能信息（skill_sets.json）
    4. 组装为完整的 WorkerProfile
    """

    def __init__(
        self,
        settings: Optional[WorkerProfileSettings] = None,
    ):
        """
        初始化文件来源

        Args:
            settings: 配置对象
        """
        self._settings = settings or WorkerProfileSettings()
        self._scanner = FileScanner(self._settings)
        self._context_loader = FileContextLoader(self._settings)
        self._skill_loader = FileSkillLoader(self._settings)

        # 缓存
        self._scan_result: Optional[WorkerProfileScanResult] = None

    def scan(self) -> WorkerProfileScanResult:
        """
        扫描并返回所有 WorkerProfile

        Returns:
            WorkerProfileScanResult: 扫描结果
        """
        if self._scan_result is not None:
            return self._scan_result

        # 扫描目录
        entries, scan_warnings = self._scanner.scan_with_warnings()

        # 为每个 entry 构建 profile
        profiles: list[WorkerProfile] = []

        for entry in entries:
            profile = self._build_profile(entry)
            if profile is not None:
                profiles.append(profile)

        # 构建结果
        result = WorkerProfileScanResult(
            profiles=profiles,
            scan_warnings=scan_warnings,
            source_roots=list(self._settings.roots),
        )

        self._scan_result = result
        return result

    def get_profile(
        self, staff_id: str, profile_id: str
    ) -> Optional[WorkerProfile]:
        """
        获取指定 WorkerProfile

        Args:
            staff_id: 员工 ID
            profile_id: 画像 ID

        Returns:
            WorkerProfile 或 None
        """
        result = self.scan()

        for profile in result.profiles:
            if profile.staff_id == staff_id and profile.profile_id == profile_id:
                return profile

        return None

    def get_profiles_by_staff(self, staff_id: str) -> list[WorkerProfile]:
        """
        获取指定员工的所有 WorkerProfile

        Args:
            staff_id: 员工 ID

        Returns:
            WorkerProfile 列表
        """
        result = self.scan()

        return [
            profile for profile in result.profiles
            if profile.staff_id == staff_id
        ]

    def _build_profile(self, entry: ScanEntry) -> Optional[WorkerProfile]:
        """
        从 ScanEntry 构建 WorkerProfile

        Args:
            entry: 扫描条目

        Returns:
            WorkerProfile 或 None
        """
        # 确定画像类型
        if entry.profile_type == "default":
            profile_type = ProfileType.DEFAULT
        else:
            profile_type = ProfileType.BOT

        # 加载上下文
        context_fragments, context_warnings = self._context_loader.load_with_warnings(
            entry.openclaw_path
        )

        # 加载技能
        skills, skill_warnings = self._skill_loader.load(entry.skills_path)

        # 合并所有警告
        all_warnings = context_warnings + skill_warnings

        # 创建 profile
        profile = WorkerProfile(
            staff_id=entry.staff_id,
            profile_id=entry.profile_id,
            profile_type=profile_type,
            source_type=SourceType.FILE,
            source_root=entry.source_root,
            context_fragments=context_fragments,
            active_skills=skills,
            warnings=all_warnings,
        )

        # 生成可检索文本
        profile.generate_searchable_text()

        return profile

    def clear_cache(self) -> None:
        """清除缓存"""
        self._scan_result = None


__all__ = ["FileWorkerProfileSource"]