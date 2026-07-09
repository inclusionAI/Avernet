"""API Key Repository Protocol — extracted from api_gateway_repository.py."""

from typing import Protocol, runtime_checkable

from ._record import APIKeyRecord


@runtime_checkable
class APIKeyRepository(Protocol):
    """API Key Repository Protocol"""

    def insert(
        self,
        *,
        api_key_hash: str,
        api_key_prefix: str,
        key_name: str | None,
        app_id: str,
        app_type: str | None,
        description: str | None,
        rate_limit_rpm: int | None,
        rate_limit_rpd: int | None,
        status: str,
        owner: str,
        tenant: str | None,
        env: str,
        creator: str,
        policy: str | None,
    ) -> int:
        """插入 API Key 记录，返回 ID"""
        ...

    def get_by_id(self, key_id: int) -> APIKeyRecord | None:
        """根据 ID 查询"""
        ...

    def get_by_prefix(self, prefix: str) -> APIKeyRecord | None:
        """根据前缀查询"""
        ...

    def get_by_prefix_and_status(self, prefix: str, status: str) -> APIKeyRecord | None:
        """根据前缀和状态查询"""
        ...

    def list_keys(
        self,
        *,
        app_id: str | None = None,
        app_type: str | None = None,
        status: str | None = None,
        creator: str | None = None,
        owner: str | None = None,
        tenant: str | None = None,
        env: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[APIKeyRecord]]:
        """分页查询列表"""
        ...

    def update(
        self,
        key_id: int,
        *,
        key_name: str | None = None,
        description: str | None = None,
        app_id: str | None = None,
        app_type: str | None = None,
        rate_limit_rpm: int | None = None,
        rate_limit_rpd: int | None = None,
        owner: str | None = None,
        tenant: str | None = None,
        modifier: str | None = None,
        policy: str | None = None,
    ) -> None:
        """更新元数据字段"""
        ...

    def update_status(
        self,
        key_id: int,
        status: str,
        modifier: str | None = None,
    ) -> None:
        """更新状态"""
        ...

    def exists_prefix(self, prefix: str) -> bool:
        """检查前缀是否存在"""
        ...
