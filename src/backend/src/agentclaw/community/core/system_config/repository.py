"""系统配置仓库协议定义

定义 ConfigRepositoryProtocol，供 core/ 和 services/ 层共同使用。
统一实现（plugins/config_repository.py）在 prod OceanBase 与本地 SQLite 上运行同一套 ORM 代码。
"""

from typing import Protocol, runtime_checkable

from agentclaw.community.core.system_config.models import (
    ConfigCategoryRecord,
    ConfigItemRecord,
)


# ============================================================
# Protocol 定义
# ============================================================

@runtime_checkable
class ConfigRepositoryProtocol(Protocol):
    """配置仓库协议"""

    # 分类目录操作
    def get_category_by_id(self, *, category_id: int) -> ConfigCategoryRecord | None:
        ...

    def get_category(self, *, category: str, env: str) -> ConfigCategoryRecord | None:
        ...

    def list_categories(self, *, env: str) -> list[ConfigCategoryRecord]:
        ...

    def upsert_category(
        self, *, category: str, category_name: str, env: str, description: str | None = None, operator: str | None = None
    ) -> int:
        ...

    def delete_category(self, *, category_id: int) -> bool:
        ...

    # 配置项操作
    def get_config(self, *, config_id: int) -> ConfigItemRecord | None:
        ...

    def get_config_by_key(self, *, parent_id: int, config_key: str) -> ConfigItemRecord | None:
        ...

    def upsert_config(
        self, *, parent_id: int, config_key: str, config_value: str, description: str | None = None, operator: str | None = None
    ) -> int:
        ...

    def delete_config(self, *, config_id: int) -> bool:
        ...

    def list_configs(self, *, parent_id: int) -> list[ConfigItemRecord]:
        ...

    def list_all_configs(self, *, env: str) -> list[dict]:
        ...
