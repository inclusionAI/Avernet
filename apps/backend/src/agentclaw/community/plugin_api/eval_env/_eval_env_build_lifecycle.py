"""EvalEnvBuildLifecycle — 评测环境构建生命周期（B3 编排消费面）。

与 ``EvalEnvLifecycleProtocol`` 的 ``create_eval_env``/``destroy_eval_env``/
``get_eval_connection``（按 ``bot_uuid``、连接信息）抽象不同：本协议面向 OCB
corp B3 编排的真实「异步创建 → 轮询构建状态 → 销毁」流程，对应 PROD 接口 B 组
``/api/service-bot/eval-env/{create,build-status,destroy}``（落地于
``DefaultEnvBotService.create_eval_env_bot`` / ``get_eval_build_status`` /
``destroy_eval_env_bot``，均按 ``bot_id + default_tag`` 路由、异步、返回 build_status
字典）。旧 ``EvalEnvLifecycleProtocol`` 保留不动（其签名与真实 ``create_eval_env_bot``
不符、未被消费；此处另起独立契约，避免破坏既有签名）。

方法（async）：
- ``create_eval_env`` → ``{"build_status": "building"|"active", "version_id": ..., ...}``
- ``get_eval_build_status`` → 轮询 ``{"build_status": "building"|"active"|"failed"|"not_found", ...}``
- ``destroy_eval_env`` → 销毁结果 ``{"success": bool, ...}``（best-effort）
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


@runtime_checkable
class EvalEnvBuildLifecycle(Plugin, Protocol):
    """评测环境构建生命周期 Plugin——B3 编排消费面。"""

    async def create_eval_env(
        self,
        *,
        bot_id: str,
        default_tag: str = "eval",
        operator_id: str = "",
        ttl_minutes: int | None = None,
    ) -> dict[str, Any]:
        """异步创建评测环境。

        Returns
        -------
        dict[str, Any]
            ``{"build_status": "building"|"active", "version_id": ..., ...}``。
            ``active`` 表示幂等就绪（无需轮询）；``building`` 表示已入队，需
            轮询 :meth:`get_eval_build_status`。
        """
        ...

    async def get_eval_build_status(
        self,
        *,
        bot_id: str,
        default_tag: str = "eval",
    ) -> dict[str, Any]:
        """轮询评测环境构建状态。

        Returns
        -------
        dict[str, Any]
            ``{"build_status": "building"|"active"|"failed"|"not_found", ...}``。
        """
        ...

    async def destroy_eval_env(
        self,
        *,
        bot_id: str,
        default_tag: str = "eval",
        reason: str = "",
    ) -> dict[str, Any]:
        """销毁评测环境（best-effort）。

        Returns
        -------
        dict[str, Any]
            ``{"success": bool, ...}``。评测总开关关闭时也允许执行销毁，避免
            遗留资源无法清理。
        """
        ...
