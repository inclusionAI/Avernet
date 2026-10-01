"""EvalEnvLifecycleProtocol — 评测环境生命周期管理。

负责评测 Bot 的创建、销毁及连接信息获取，将评测环境
管理与生产环境隔离。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


@runtime_checkable
class EvalEnvLifecycleProtocol(Plugin, Protocol):
    """评测环境生命周期 Plugin。

    将 DefaultEnvBotService 中的评测 Bot 创建/销毁/连接逻辑
    抽离为独立 Plugin，消除生产代码对评测逻辑的直接依赖。
    """

    def create_eval_env(
        self,
        *,
        bot_id: str,
        owner_id: str,
        default_tag: str,
        ext_info: dict[str, Any] | None = None,
    ) -> str:
        """创建评测环境，返回 bot_uuid。

        Parameters
        ----------
        bot_id : str
            待评测的 Bot 标识。
        owner_id : str
            评测环境拥有者标识。
        default_tag : str
            评测路由标签（如 ``eval:tag:bot_id``）。
        ext_info : dict[str, Any] | None
            扩展信息（可选）。

        Returns
        -------
        str
            创建的评测 Bot UUID。
        """
        ...

    def destroy_eval_env(
        self,
        *,
        bot_uuid: str,
        operator: str,
    ) -> dict[str, Any]:
        """销毁评测环境。

        Parameters
        ----------
        bot_uuid : str
            待销毁的评测 Bot UUID。
        operator : str
            操作者标识。

        Returns
        -------
        dict[str, Any]
            销毁结果元数据。
        """
        ...

    def get_eval_connection(
        self,
        *,
        bot_id: str,
        default_tag: str,
        operator: str,
    ) -> Any:
        """获取评测环境的连接信息。

        Parameters
        ----------
        bot_id : str
            Bot 标识。
        default_tag : str
            评测路由标签。
        operator : str
            操作者标识。

        Returns
        -------
        DeviceConnectionInfo
            评测环境设备连接信息。

        Raises
        ------
        DefaultEnvBotServiceError
            Noop 实现在评测环境功能关闭时抛出。
        """
        ...