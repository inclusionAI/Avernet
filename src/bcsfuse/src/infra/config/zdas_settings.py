"""
ZDAS Database Settings

ZDAS 数据库配置，用于多环境部署。

配置来源优先级：
1. 环境变量（最高优先级）
2. application.yaml 配置文件
3. 默认值
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class ZDASDatasourceConfig(BaseModel):
    """单个数据源配置"""

    database: str = Field(..., description="数据源名称，如 agentclaw_ds")
    user: str = Field(..., description="数据库用户，格式 xx:bcsfuse:xx")
    password: str = Field(default="", description="密码，Mesh 模式为空")
    host: str = Field(default="127.0.0.1", description="ZDAS 服务器地址")
    port: str = Field(default="11306", description="ZDAS 服务器端口")


class ZDASSettings(BaseSettings):
    """
    ZDAS 数据库配置

    从环境变量读取配置，支持 .env 文件。

    环境变量：
    - ZDAS_ENABLED: 是否启用 ZDAS
    - ZDAS_DATABASE: 数据源名称
    - ZDAS_USER: 数据库用户
    - ZDAS_PASSWORD: 密码
    - ZDAS_HOST: ZDAS 服务器地址
    - ZDAS_PORT: ZDAS 服务器端口
    """

    enabled: bool = Field(
        default=False,
        description="是否启用 ZDAS 数据库",
    )

    datasources: list[ZDASDatasourceConfig] = Field(
        default_factory=list,
        description="数据源列表",
    )

    # 便捷属性：默认数据源名称
    default_datasource: str = Field(
        default="agentclaw_ds",
        description="默认数据源名称",
    )

    model_config = {
        "env_prefix": "ZDAS_",
        "env_file": ".env",
        "extra": "ignore",
    }

    def get_datasource_config(self, name: Optional[str] = None) -> Optional[ZDASDatasourceConfig]:
        """
        获取指定数据源的配置

        Args:
            name: 数据源名称，默认使用 default_datasource

        Returns:
            数据源配置，如果不存在返回 None
        """
        ds_name = name or self.default_datasource
        for ds in self.datasources:
            if ds.database == ds_name:
                return ds
        return None

    @property
    def is_configured(self) -> bool:
        """检查 ZDAS 是否已配置"""
        return self.enabled and len(self.datasources) > 0


class WorkerRegistryDatabaseMode:
    """Worker Registry 数据库模式"""

    SQLITE = "sqlite"
    ZDAS = "zdas"


class WorkerRegistryDatabaseSettings(BaseSettings):
    """
    Worker Registry 数据库配置

    支持两种模式：
    - sqlite: 本地 SQLite 数据库（单实例）
    - zdas: ZDAS MySQL 数据库（多实例共享）

    环境变量：
    - WORKER_REGISTRY_DATABASE_MODE: 数据库模式 (sqlite/zdas)
    - WORKER_REGISTRY_SQLITE_DB_PATH: SQLite 数据库路径
    - WORKER_REGISTRY_ZDAS_DATASOURCE: ZDAS 数据源名称
    """

    database_mode: str = Field(
        default=WorkerRegistryDatabaseMode.SQLITE,
        validation_alias="WORKER_REGISTRY_DATABASE_MODE",
        description="数据库模式: sqlite 或 zdas",
    )

    sqlite_db_path: str = Field(
        default="data/worker_registry.db",
        description="SQLite 数据库路径",
    )

    zdas_datasource: str = Field(
        default="agentclaw_ds",
        description="ZDAS 数据源名称",
    )

    model_config = {
        "env_prefix": "WORKER_REGISTRY_",
        "env_file": ".env",
        "extra": "ignore",
    }

    def model_post_init(self, __context) -> None:
        """初始化后记录配置信息"""
        # 打印所有相关的环境变量
        env_vars = [
            "WORKER_REGISTRY_DATABASE_MODE",
            "WORKER_REGISTRY_SQLITE_DB_PATH",
            "WORKER_REGISTRY_ZDAS_DATASOURCE",
        ]
        for var in env_vars:
            value = os.environ.get(var, "(未设置)")
            logger.debug("[ZDAS-SETTINGS]   - %s: %s", var, value)

        logger.info("[ZDAS] WorkerRegistryDatabaseSettings initialized:")
        logger.info("[ZDAS]   - WORKER_REGISTRY_DATABASE_MODE env: %s", os.environ.get("WORKER_REGISTRY_DATABASE_MODE", "not set"))
        logger.info("[ZDAS]   - database_mode: %s", self.database_mode)
        logger.info("[ZDAS]   - is_zdas_mode: %s", self.is_zdas_mode)
        logger.info("[ZDAS]   - is_sqlite_mode: %s", self.is_sqlite_mode)
        logger.info("[ZDAS]   - sqlite_db_path: %s", self.sqlite_db_path)
        logger.info("[ZDAS]   - zdas_datasource: %s", self.zdas_datasource)

    @property
    def is_zdas_mode(self) -> bool:
        """是否使用 ZDAS 模式"""
        return self.database_mode == WorkerRegistryDatabaseMode.ZDAS

    @property
    def is_sqlite_mode(self) -> bool:
        """是否使用 SQLite 模式"""
        return self.database_mode == WorkerRegistryDatabaseMode.SQLITE


__all__ = [
    "ZDASSettings",
    "ZDASDatasourceConfig",
    "WorkerRegistryDatabaseSettings",
    "WorkerRegistryDatabaseMode",
]