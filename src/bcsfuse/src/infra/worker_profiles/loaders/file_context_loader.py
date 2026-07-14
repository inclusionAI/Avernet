"""
File Context Loader

Worker Profile Ingestion Baseline

从 openclaw 目录加载上下文 md 文件。
"""

from __future__ import annotations

import os
from typing import Optional

from src.domain.models.context_fragment import ContextFragment, ContextKind
from src.domain.models.worker_profile import WorkerProfileWarning
from src.infra.worker_profiles.config.worker_profile_settings import WorkerProfileSettings


class FileContextLoader:
    """
    文件上下文加载器

    从 openclaw 目录加载各类 md 文件。

    规则：
    - 只加载 .md 文件
    - 已知文件类型使用对应 ContextKind
    - 未识别文件使用 ContextKind.OTHER 并记录警告
    - 文件不存在返回空列表
    - 空文件允许存在
    """

    def __init__(self, settings: Optional[WorkerProfileSettings] = None):
        """
        初始化加载器

        Args:
            settings: 配置对象
        """
        self._settings = settings or WorkerProfileSettings()

    def load(self, openclaw_path: str) -> list[ContextFragment]:
        """
        加载 openclaw 目录下的所有上下文文件

        Args:
            openclaw_path: openclaw 目录路径

        Returns:
            上下文片段列表
        """
        fragments, _ = self.load_with_warnings(openclaw_path)
        return fragments

    def load_with_warnings(
        self, openclaw_path: str
    ) -> tuple[list[ContextFragment], list[WorkerProfileWarning]]:
        """
        加载 openclaw 目录下的所有上下文文件（带警告）

        Args:
            openclaw_path: openclaw 目录路径

        Returns:
            (上下文片段列表, 警告列表)
        """
        fragments: list[ContextFragment] = []
        warnings: list[WorkerProfileWarning] = []

        # 检查目录是否存在
        if not os.path.exists(openclaw_path):
            warnings.append(WorkerProfileWarning(
                code="OPENCLAW_DIR_NOT_FOUND",
                message=f"OpenClaw directory not found: {openclaw_path}",
                source_path=openclaw_path,
            ))
            return fragments, warnings

        if not os.path.isdir(openclaw_path):
            warnings.append(WorkerProfileWarning(
                code="OPENCLAW_NOT_DIRECTORY",
                message=f"OpenClaw path is not a directory: {openclaw_path}",
                source_path=openclaw_path,
            ))
            return fragments, warnings

        # 遍历目录中的 md 文件
        known_files = set(self._settings.context_file_mapping.keys())

        try:
            for filename in os.listdir(openclaw_path):
                if not filename.endswith(".md"):
                    continue

                filepath = os.path.join(openclaw_path, filename)

                if not os.path.isfile(filepath):
                    continue

                # 确定上下文类型
                if filename in known_files:
                    kind = self._settings.context_file_mapping[filename]
                else:
                    # 未识别的 md 文件
                    kind = ContextKind.OTHER
                    warnings.append(WorkerProfileWarning(
                        code="UNRECOGNIZED_CONTEXT_FILE",
                        message=f"Unrecognized markdown file (using OTHER kind): {filename}",
                        source_path=filepath,
                        suggestion="Consider adding this file to context_file_mapping config",
                    ))

                # 读取文件内容
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read()
                except Exception as e:
                    warnings.append(WorkerProfileWarning(
                        code="FILE_READ_ERROR",
                        message=f"Failed to read file {filename}: {str(e)}",
                        source_path=filepath,
                    ))
                    continue

                # 创建上下文片段
                fragment = ContextFragment(
                    kind=kind,
                    filename=filename,
                    content=content,
                    source_path=filepath,
                )
                fragments.append(fragment)

        except Exception as e:
            warnings.append(WorkerProfileWarning(
                code="DIRECTORY_SCAN_ERROR",
                message=f"Failed to scan directory {openclaw_path}: {str(e)}",
                source_path=openclaw_path,
            ))

        return fragments, warnings


__all__ = ["FileContextLoader"]