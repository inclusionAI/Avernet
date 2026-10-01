"""EvalBindingResolverProtocol — 评测绑定解析。

将评测环境的 binding 解析逻辑从 DeviceInstanceService 中抽离，
独立于此 Plugin 管理。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


@runtime_checkable
class EvalBindingResolverProtocol(Plugin, Protocol):
    """评测绑定解析 Plugin。

    将 ``DeviceInstanceService._resolve_eval_binding_id`` 中的
    评测绑定解析逻辑抽离为独立 Plugin，实现评测/生产绑定路径隔离。
    """

    def resolve_eval_binding(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str,
        default_tag: str,
    ) -> int | None:
        """解析评测环境的 binding_id。

        Parameters
        ----------
        bot_id : str
            Bot 标识。
        entity_id : str
            实体标识。
        env : str
            环境标识。
        default_tag : str
            评测路由标签。

        Returns
        -------
        int | None
            评测 binding_id，无匹配时返回 ``None``（降级走生产）。
        """
        ...

    def resolve_default_binding(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str,
        lifecycle_stage: str = "online",
    ) -> int | None:
        """解析默认（降级）binding_id。

        当评测环境不可用时，降级到指定 lifecycle_stage 的生产 binding。

        Parameters
        ----------
        bot_id : str
            Bot 标识。
        entity_id : str
            实体标识。
        env : str
            环境标识。
        lifecycle_stage : str
            降级目标生命周期阶段，默认 ``"online"``。

        Returns
        -------
        int | None
            降级 binding_id，无匹配时返回 ``None``。
        """
        ...