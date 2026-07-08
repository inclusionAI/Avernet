"""
API Key Repository Protocol 定义
"""

from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

from secbaas.api import OperationContext

from ._models import APIKeyRecord

if TYPE_CHECKING:
    from secbaas.api.api_gateway import (
        APIKeyCreate,
        APIKeyCreateResponse,
        APIKeyListResponse,
        APIKeyQuery,
        APIKeyResponse,
        APIKeyUpdate,
    )


@runtime_checkable
class APIKeyValidator(Protocol):
    """API Key validator Protocol.

    High-frequency, read-only operation — extensible with caching/rate-limiting.
    """

    async def verify(self, api_key: str) -> APIKeyRecord | None:
        """Verify an API Key.

        Args:
            api_key: Full API Key string.

        Returns:
            APIKeyRecord on success, None on failure.
        """
        ...

    def verify_sync(self, api_key: str) -> APIKeyRecord | None:
        """Synchronous API Key verification (for non-async contexts).

        Args:
            api_key: Full API Key string.

        Returns:
            APIKeyRecord on success, None on failure.
        """
        ...


@runtime_checkable
class APIKeyService(Protocol):
    """API Key 管理服务 Protocol"""

    async def create_key(
        self, data: "APIKeyCreate", ctx: OperationContext
    ) -> "APIKeyCreateResponse":
        """创建 API Key"""
        ...

    async def get_key(
        self, key_id: int, ctx: OperationContext
    ) -> Optional["APIKeyResponse"]:
        """查询单个 API Key"""
        ...

    async def get_key_by_prefix(
        self, prefix: str, ctx: OperationContext
    ) -> Optional["APIKeyResponse"]:
        """根据前缀查询 API Key"""
        ...

    async def list_keys(
        self,
        query: "APIKeyQuery",
        ctx: OperationContext,
        page: int = 1,
        page_size: int = 20,
    ) -> "APIKeyListResponse":
        """查询 API Key 列表"""
        ...

    async def update_key(
        self, key_id: int, data: "APIKeyUpdate", ctx: OperationContext
    ) -> Optional["APIKeyResponse"]:
        """更新 API Key 元数据"""
        ...

    async def update_key_by_prefix(
        self, prefix: str, data: "APIKeyUpdate", ctx: OperationContext
    ) -> Optional["APIKeyResponse"]:
        """根据前缀更新 API Key 元数据"""
        ...

    async def activate(
        self, key_id: int, ctx: OperationContext
    ) -> Optional["APIKeyResponse"]:
        """启用 API Key"""
        ...

    async def activate_by_prefix(
        self, prefix: str, ctx: OperationContext
    ) -> Optional["APIKeyResponse"]:
        """根据前缀启用 API Key"""
        ...

    async def deactivate(
        self, key_id: int, ctx: OperationContext
    ) -> Optional["APIKeyResponse"]:
        """停用 API Key"""
        ...

    async def deactivate_by_prefix(
        self, prefix: str, ctx: OperationContext
    ) -> Optional["APIKeyResponse"]:
        """根据前缀停用 API Key"""
        ...

    async def revoke(
        self, key_id: int, ctx: OperationContext
    ) -> Optional["APIKeyResponse"]:
        """吊销 API Key"""
        ...

    async def revoke_by_prefix(
        self, prefix: str, ctx: OperationContext
    ) -> Optional["APIKeyResponse"]:
        """根据前缀吊销 API Key"""
        ...
