"""
File Scanner

Worker Profile Ingestion Baseline

扫描文件系统目录结构，识别 staff/default/bot 目录。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from src.domain.models.worker_profile import WorkerProfileWarning
from src.infra.worker_profiles.config.worker_profile_settings import WorkerProfileSettings


@dataclass
class ScanEntry:
    """
    扫描条目

    表示一个有效的 worker profile 目录。
    """
    staff_id: str
    profile_id: str
    profile_type: str  # "default" or "bot"
    source_root: str
    openclaw_path: str

    @property
    def skills_path(self) -> str:
        """获取 skills 目录路径"""
        return os.path.join(self.openclaw_path, "skills")


class FileScanner:
    """
    文件目录扫描器

    扫描文件系统目录结构，识别有效的 worker profile 目录。

    目录识别规则：
    - staff_xxx: 员工命名空间
    - default: 员工默认数字分身
    - YYYYMMDD_xxxxxxxx: 员工创建的 bot
    - default_bak 等: 备份目录（默认忽略）
    """

    # Staff 目录模式
    STAFF_DIR_PATTERN = re.compile(r"^staff_(\d+)$")

    def __init__(self, settings: Optional[WorkerProfileSettings] = None):
        """
        初始化扫描器

        Args:
            settings: 配置对象
        """
        self._settings = settings or WorkerProfileSettings()

    def scan(self) -> list[ScanEntry]:
        """
        扫描所有 roots

        Returns:
            扫描条目列表
        """
        entries, _ = self.scan_with_warnings()
        return entries

    def scan_with_warnings(
        self,
    ) -> tuple[list[ScanEntry], list[WorkerProfileWarning]]:
        """
        扫描所有 roots（带警告）

        Returns:
            (扫描条目列表, 警告列表)
        """
        all_entries: list[ScanEntry] = []
        all_warnings: list[WorkerProfileWarning] = []

        # 记录已见过的 profile keys（用于检测重复）
        seen_keys: dict[str, ScanEntry] = {}

        for root in self._settings.roots:
            root_entries, root_warnings = self._scan_root(root)
            all_warnings.extend(root_warnings)

            # 处理重复
            for entry in root_entries:
                key = f"{entry.staff_id}:{entry.profile_id}"
                if key in seen_keys:
                    # 记录重复警告
                    all_warnings.append(WorkerProfileWarning(
                        code="DUPLICATE_PROFILE",
                        message=f"Duplicate profile '{key}' found in {root}. "
                                f"Previously found in {seen_keys[key].source_root}. Skipping.",
                        source_path=root,
                        suggestion="Remove duplicate profile from one of the roots",
                    ))
                else:
                    seen_keys[key] = entry
                    all_entries.append(entry)

        return all_entries, all_warnings

    def _scan_root(
        self, root: str
    ) -> tuple[list[ScanEntry], list[WorkerProfileWarning]]:
        """
        扫描单个根目录

        Args:
            root: 根目录路径

        Returns:
            (扫描条目列表, 警告列表)
        """
        entries: list[ScanEntry] = []
        warnings: list[WorkerProfileWarning] = []

        # 检查目录是否存在
        if not os.path.exists(root):
            warnings.append(WorkerProfileWarning(
                code="ROOT_NOT_FOUND",
                message=f"Root directory not found: {root}",
                source_path=root,
            ))
            return entries, warnings

        if not os.path.isdir(root):
            warnings.append(WorkerProfileWarning(
                code="ROOT_NOT_DIRECTORY",
                message=f"Root path is not a directory: {root}",
                source_path=root,
            ))
            return entries, warnings

        try:
            # 遍历顶层目录
            for dirname in os.listdir(root):
                dir_path = os.path.join(root, dirname)

                # 检查是否为 staff 目录
                match = self.STAFF_DIR_PATTERN.match(dirname)
                if not match:
                    continue

                if not os.path.isdir(dir_path):
                    continue

                staff_id = match.group(1)

                # 扫描 staff 目录下的 profile 子目录
                staff_entries, staff_warnings = self._scan_staff_dir(
                    staff_id, dir_path, root
                )
                entries.extend(staff_entries)
                warnings.extend(staff_warnings)

        except Exception as e:
            warnings.append(WorkerProfileWarning(
                code="ROOT_SCAN_ERROR",
                message=f"Failed to scan root directory {root}: {str(e)}",
                source_path=root,
            ))

        return entries, warnings

    def _scan_staff_dir(
        self,
        staff_id: str,
        staff_path: str,
        source_root: str,
    ) -> tuple[list[ScanEntry], list[WorkerProfileWarning]]:
        """
        扫描 staff 目录下的 profile 子目录

        Args:
            staff_id: 员工 ID
            staff_path: staff 目录路径
            source_root: 源根目录

        Returns:
            (扫描条目列表, 警告列表)
        """
        entries: list[ScanEntry] = []
        warnings: list[WorkerProfileWarning] = []

        try:
            for dirname in os.listdir(staff_path):
                profile_path = os.path.join(staff_path, dirname)

                if not os.path.isdir(profile_path):
                    continue

                # 检查是否为备份目录
                if self._settings.is_backup_directory(dirname):
                    if not self._settings.include_backup:
                        continue

                # 检查是否为 openclaw 目录（不应该直接出现在 staff 下）
                if dirname == "openclaw":
                    continue

                # 确定 profile 类型和 ID
                profile_type: Optional[str] = None
                profile_id: Optional[str] = None

                if dirname == "default":
                    profile_type = "default"
                    profile_id = "default"
                elif self._settings.is_bot_directory(dirname):
                    profile_type = "bot"
                    profile_id = dirname
                elif self._settings.include_backup and self._settings.is_backup_directory(dirname):
                    # 备份目录作为特殊类型处理
                    profile_type = "default" if "default" in dirname else "bot"
                    profile_id = dirname

                if profile_type is None or profile_id is None:
                    # 无法识别的目录，记录警告
                    warnings.append(WorkerProfileWarning(
                        code="UNRECOGNIZED_PROFILE_DIR",
                        message=f"Unrecognized profile directory: {dirname}",
                        source_path=profile_path,
                    ))
                    continue

                # 检查 openclaw 目录是否存在
                openclaw_path = os.path.join(profile_path, "openclaw")
                if not os.path.exists(openclaw_path):
                    warnings.append(WorkerProfileWarning(
                        code="OPENCLAW_DIR_MISSING",
                        message=f"OpenClaw directory missing for profile: {profile_id}",
                        source_path=profile_path,
                        suggestion="openclaw directory is required for profile",
                    ))
                    continue

                # 创建扫描条目
                entry = ScanEntry(
                    staff_id=staff_id,
                    profile_id=profile_id,
                    profile_type=profile_type,
                    source_root=source_root,
                    openclaw_path=openclaw_path,
                )
                entries.append(entry)

        except Exception as e:
            warnings.append(WorkerProfileWarning(
                code="STAFF_DIR_SCAN_ERROR",
                message=f"Failed to scan staff directory {staff_path}: {str(e)}",
                source_path=staff_path,
            ))

        return entries, warnings


__all__ = ["FileScanner", "ScanEntry"]