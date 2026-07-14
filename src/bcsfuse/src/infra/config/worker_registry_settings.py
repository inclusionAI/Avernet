"""
Worker Registry Settings

Worker Registry 模块的配置。

Stage 1 Phase 3：SQLite 持久化配置。
"""

from __future__ import annotations

import os
from typing import Optional

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class WorkerRegistrySettings(BaseSettings):
    """
    Worker Registry 配置

    从环境变量读取配置，支持 .env 文件。

    Attributes:
        sqlite_db_path: SQLite 数据库路径
        sqlite_echo: 是否打印 SQL 日志
    """

    sqlite_db_path: str = Field(
        default="data/worker_registry.db",
        description="SQLite 数据库路径",
    )
    sqlite_echo: bool = Field(
        default=False,
        description="是否打印 SQL 日志",
    )

    model_config = {
        "env_prefix": "WORKER_REGISTRY_",
        "env_file": ".env",
        "extra": "ignore",
    }

    def get_effective_db_path(self) -> str:
        """
        获取有效的数据库路径

        如果是 :memory: 则直接返回，否则确保目录存在。
        相对路径会被转换为基于项目根目录的绝对路径。

        Returns:
            绝对路径或 :memory:
        """
        if self.sqlite_db_path == ":memory:":
            return self.sqlite_db_path

        # 将相对路径转换为绝对路径（基于项目根目录）
        from src.infra.config.data_paths import resolve_data_path
        db_path = resolve_data_path(self.sqlite_db_path)

        # 确保目录存在
        import os
        dir_path = os.path.dirname(db_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

        return db_path


class WorkerDependenciesSettings(BaseModel):
    """
    Worker 依赖设置

    用于测试和生产的依赖配置。
    """

    use_in_memory: bool = Field(
        default=False,
        description="是否使用内存存储（仅用于测试）",
    )
    sqlite_db_path: Optional[str] = Field(
        default=None,
        description="SQLite 数据库路径（覆盖全局设置）",
    )


__all__ = ["WorkerRegistrySettings", "WorkerDependenciesSettings"]