"""Repository contracts owned by the ``config`` domain.

Moved here by the ``core/repository`` consolidation. Every member is
``@abstractmethod``: an implementation that omits one fails at construction
naming the missing member, instead of raising ``AttributeError`` at the call
site. Domain imports are ``TYPE_CHECKING``-only — see the module docstring in
``core/repository/README.md`` for why that direction is load-bearing.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.common_config.models import CommonConfigRecord
    from agentclaw.community.core.system_config.models import ConfigCategoryRecord, ConfigItemRecord


@runtime_checkable
class ConfigRepositoryProtocol(Protocol):
    """配置仓库协议"""

    # 分类目录操作
    @abstractmethod
    def get_category_by_id(self, *, category_id: int) -> ConfigCategoryRecord | None:
        ...

    @abstractmethod
    def get_category(self, *, category: str, env: str) -> ConfigCategoryRecord | None:
        ...

    @abstractmethod
    def list_categories(self, *, env: str) -> list[ConfigCategoryRecord]:
        ...

    @abstractmethod
    def upsert_category(
        self, *, category: str, category_name: str, env: str, description: str | None = None, operator: str | None = None
    ) -> int:
        ...

    @abstractmethod
    def delete_category(self, *, category_id: int) -> bool:
        ...

    # 配置项操作
    @abstractmethod
    def get_config(self, *, config_id: int) -> ConfigItemRecord | None:
        ...

    @abstractmethod
    def get_config_by_key(self, *, parent_id: int, config_key: str) -> ConfigItemRecord | None:
        ...

    @abstractmethod
    def upsert_config(
        self, *, parent_id: int, config_key: str, config_value: str, description: str | None = None, operator: str | None = None
    ) -> int:
        ...

    @abstractmethod
    def delete_config(self, *, config_id: int) -> bool:
        ...

    @abstractmethod
    def list_configs(self, *, parent_id: int) -> list[ConfigItemRecord]:
        ...

    @abstractmethod
    def list_all_configs(self, *, env: str) -> list[dict]:
        ...


@runtime_checkable
class CommonConfigRepositoryProtocol(Protocol):
    """``ac_common_config`` 仓库协议。"""

    @abstractmethod
    def get_by_id(self, *, config_id: int) -> CommonConfigRecord | None: ...

    @abstractmethod
    def get_by_biz_param(
        self, *, business_code: str, param_code: str, env: str
    ) -> CommonConfigRecord | None: ...

    @abstractmethod
    def list_configs(
        self,
        *,
        env: str,
        business_code: str | None = None,
        enable: str | None = None,
        keyword: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[CommonConfigRecord]: ...

    @abstractmethod
    def count_configs(
        self,
        *,
        env: str,
        business_code: str | None = None,
        enable: str | None = None,
        keyword: str | None = None,
    ) -> int: ...

    @abstractmethod
    def create_config(
        self,
        *,
        business_code: str,
        param_name: str,
        param_value: str | None,
        business_name: str | None,
        param_code: str,
        enable: str,
        ext_info: str | None,
        env: str,
    ) -> int: ...

    @abstractmethod
    def update_config(self, *, config_id: int, updates: dict) -> bool: ...

    @abstractmethod
    def upsert_config(
        self,
        *,
        business_code: str,
        param_name: str,
        param_value: str | None,
        business_name: str | None,
        param_code: str,
        enable: str,
        ext_info: str | None,
        env: str,
    ) -> int: ...

    @abstractmethod
    def delete_config(self, *, config_id: int) -> bool: ...

    @abstractmethod
    def delete_by_biz_param(
        self, *, business_code: str, param_code: str, env: str
    ) -> bool: ...
