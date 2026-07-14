"""
Worker Profile Settings

Worker Profile Ingestion Baseline

配置模型，支持：
- 多 roots 配置
- 环境变量加载
- 目录模式配置
- 上下文文件映射
"""

from __future__ import annotations

import os
import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from src.domain.models.context_fragment import ContextKind


class WorkerProfileSettings(BaseModel):
    """
    Worker Profile 配置模型

    从环境变量或参数加载配置。

    环境变量：
        WORKER_PROFILE_ROOTS: 数据根目录列表（逗号分隔）
        WORKER_PROFILE_INCLUDE_BACKUP: 是否包含备份目录
        WORKER_PROFILE_SCAN_BOTS: 是否扫描 bot 目录
        WORKER_PROFILE_PREFER_DEFAULT: 是否优先返回 default
        WORKER_PROFILE_ALLOW_NO_ACTIVE_SKILLSET: 是否允许无激活技能组

    Attributes:
        roots: Worker profile 数据根目录列表
        include_backup: 是否包含 default_bak 等备份目录
        scan_bots: 是否扫描 bot 目录
        prefer_default: 员工同时有 default 和 bot 时是否优先返回 default
        allow_no_active_skillset: 是否允许没有激活技能组的 profile
        context_file_mapping: 文件名到上下文类型的映射
        bot_directory_pattern: Bot 目录识别正则模式
        backup_directories: 备份目录名称列表
    """

    # 数据根目录
    roots: list[str] = Field(
        default_factory=list,
        description="Worker profile 数据根目录列表 (configure via WORKER_PROFILE_ROOTS env var)"
    )

    # 目录处理选项
    include_backup: bool = Field(
        default=False,
        description="是否包含 default_bak 等备份目录"
    )
    scan_bots: bool = Field(
        default=True,
        description="是否扫描 bot 目录"
    )
    prefer_default: bool = Field(
        default=True,
        description="员工同时有 default 和 bot 时是否优先返回 default"
    )

    # 技能处理选项
    allow_no_active_skillset: bool = Field(
        default=False,
        description="是否允许没有激活技能组的 profile"
    )

    # 文件映射
    context_file_mapping: dict[str, ContextKind] = Field(
        default_factory=lambda: {
            "AGENTS.md": ContextKind.AGENT,
            "BOOT.md": ContextKind.BOOT,
            "HEARTBEAT.md": ContextKind.HEARTBEAT,
            "SOUL.md": ContextKind.SOUL,
            "TOOLS.md": ContextKind.TOOLS,
            "RULES.md": ContextKind.RULES,
            "MEMORY.md": ContextKind.MEMORY,
            "USER.md": ContextKind.USER,
        },
        description="文件名到上下文类型的映射"
    )

    # 目录模式
    bot_directory_pattern: str = Field(
        default=r"^\d{8}_[a-zA-Z0-9]+$",
        description="Bot 目录识别正则模式（YYYYMMDD_xxxxxxxx）"
    )

    backup_directories: list[str] = Field(
        default_factory=lambda: ["default_bak", ".bak", "_bak"],
        description="备份目录名称列表"
    )

    def __init__(self, **data):
        """
        初始化配置

        优先从环境变量加载，参数可覆盖。
        """
        # 从环境变量加载
        env_data = self._load_from_env()

        # 合并参数（参数优先于环境变量）
        merged = {**env_data, **data}

        # 处理空 roots
        if not merged.get("roots"):
            merged["roots"] = []

        super().__init__(**merged)

    @staticmethod
    def _load_from_env() -> dict:
        """从环境变量加载配置"""
        data: dict = {}

        # 加载 roots
        roots_env = os.environ.get("WORKER_PROFILE_ROOTS", "")
        if roots_env:
            # 逗号分隔，去除空格和空项
            roots = [
                r.strip() for r in roots_env.split(",")
                if r.strip()
            ]
            if roots:
                data["roots"] = roots

        # 加载布尔选项
        bool_options = {
            "WORKER_PROFILE_INCLUDE_BACKUP": "include_backup",
            "WORKER_PROFILE_SCAN_BOTS": "scan_bots",
            "WORKER_PROFILE_PREFER_DEFAULT": "prefer_default",
            "WORKER_PROFILE_ALLOW_NO_ACTIVE_SKILLSET": "allow_no_active_skillset",
        }

        for env_key, field_name in bool_options.items():
            env_value = os.environ.get(env_key)
            if env_value is not None:
                data[field_name] = env_value.lower() == "true"

        return data

    @field_validator("roots", mode="before")
    @classmethod
    def filter_empty_roots(cls, v):
        """过滤空的 root 路径"""
        if isinstance(v, list):
            filtered = [r for r in v if r and r.strip()]
            return filtered if filtered else []
        return v

    def is_backup_directory(self, dirname: str) -> bool:
        """
        判断目录是否为备份目录

        Args:
            dirname: 目录名

        Returns:
            是否为备份目录
        """
        return dirname in self.backup_directories

    def is_bot_directory(self, dirname: str) -> bool:
        """
        判断目录是否为 bot 目录

        Args:
            dirname: 目录名

        Returns:
            是否为 bot 目录
        """
        if not self.scan_bots:
            return False
        return bool(re.match(self.bot_directory_pattern, dirname))

    def get_context_kind(self, filename: str) -> ContextKind:
        """
        获取文件对应的上下文类型

        Args:
            filename: 文件名

        Returns:
            上下文类型（未识别返回 OTHER）
        """
        return self.context_file_mapping.get(filename, ContextKind.OTHER)

    model_config = {
        "extra": "forbid",
    }


__all__ = [
    "WorkerProfileSettings",
]