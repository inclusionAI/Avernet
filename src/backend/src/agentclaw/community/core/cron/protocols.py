"""Protocol definitions for Cron module dependencies.

Defines abstract interfaces for external dependencies.
Implementations are provided in core/bot_management and core/devices.
"""
from typing import Protocol, Any, Dict, runtime_checkable


@runtime_checkable
class BotInfoProvider(Protocol):
    """Protocol for Bot information provider.

    Implementations:
    - core.bot_management.services.BotService
    """

    def get_bot(self, bot_id: str, user_id: str) -> Dict[str, Any]:
        """获取 Bot 信息

        Args:
            bot_id: Bot ID
            user_id: 用户ID（用于权限校验）

        Returns:
            Bot 信息字典，包含 bot_id, bot_name, binding_id 等
        """
        ...

    def list_bots_by_owner(self, owner_id: str, page: int, page_size: int) -> Dict[str, Any]:
        """获取用户的所有 Bot

        Args:
            owner_id: Bot 所有者ID
            page: 页码
            page_size: 每页数量

        Returns:
            {"items": [...], "total": N}
        """
        ...

    def list_bots_by_owner_or_collaborator(
        self,
        owner_id: str,
        page: int,
        page_size: int,
    ) -> Dict[str, Any]:
        """获取用户拥有或协作管理的所有 Bot

        Args:
            owner_id: 当前用户ID
            page: 页码
            page_size: 每页数量

        Returns:
            {"items": [...], "total": N}
        """
        ...

@runtime_checkable
class DeviceConnectionProvider(Protocol):
    """Protocol for device connection provider.

    Implementations:
    - core.devices.services.DeviceService
    """

    def get_device(self, *, binding_id: int) -> Any:
        """获取设备信息

        Args:
            binding_id: 设备绑定ID

        Returns:
            设备绑定记录，包含 status 等
        """
        ...

    def get_device_connection_v2(
        self,
        user_id: str,
        nick_name: str,
        binding_id: int,
        operator_tenant_id: str = "default",
    ) -> Dict[str, Any]:
        """获取设备连接信息

        Args:
            user_id: 用户ID
            nick_name: 用户昵称
            binding_id: 设备绑定ID
            operator_tenant_id: 租户ID

        Returns:
            连接信息字典，包含 url, headers, use_proxy 等
        """
        ...

    def get_instances(
        self,
        *,
        binding_id: int,
        health_check: bool = False,
    ) -> Dict[str, Any]:
        """获取多实例设备列表。

        Args:
            binding_id: 设备绑定ID
            health_check: 是否触发实时健康检查

        Returns:
            {"bot_uuid": "...", "devices": [...]}
        """
        ...


class DeviceBindingStatus:
    """设备绑定状态枚举"""
    ACTIVE = "ACTIVE"
    PENDING = "PENDING"
    RELEASED = "RELEASED"
    FAILED = "FAILED"
