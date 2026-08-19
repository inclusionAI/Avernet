"""NoopEvalEnvLifecycle — 评测环境生命周期 Noop 实现。

评测功能关闭时：
- create_eval_env 返回空字符串
- destroy_eval_env 返回空字典
- get_eval_connection 抛出 DefaultEnvBotServiceError
"""

from __future__ import annotations

from typing import Any

from agentclaw.community.plugin_api.eval_env import EvalEnvLifecycleProtocol
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam


class DefaultEnvBotServiceError(RuntimeError):
    """评测环境服务异常 — 评测功能关闭时的统一错误。"""

    pass


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.NOOP,
    rationale="评测环境离线：创建/销毁为空操作，连接请求抛异常",
)
class NoopEvalEnvLifecycle(MockSeam, EvalEnvLifecycleProtocol):
    """评测环境生命周期的 Noop 实现。

    当 DI 选择此实现时，等效于 ``if not config.enabled``：
    - 创建/销毁为空操作（返回空值）
    - 获取连接时抛出 ``DefaultEnvBotServiceError``，告知调用方评测功能已关闭
    """

    def create_eval_env(
        self,
        *,
        bot_id: str,
        owner_id: str,
        default_tag: str,
        ext_info: dict[str, Any] | None = None,
    ) -> str:
        """Noop：返回空字符串，调用方需检查。"""
        return ""

    def destroy_eval_env(
        self,
        *,
        bot_uuid: str,
        operator: str,
    ) -> dict[str, Any]:
        """Noop：返回空字典。"""
        return {}

    def get_eval_connection(
        self,
        *,
        bot_id: str,
        default_tag: str,
        operator: str,
    ) -> Any:
        """Noop：抛出异常，等效于评测功能关闭。"""
        raise DefaultEnvBotServiceError("评测环境功能已关闭")