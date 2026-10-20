"""NoopEvalEnvBuildLifecycle — 评测环境构建生命周期 Noop 实现。

评测功能关闭时降级：
- ``create_eval_env`` 返回 ``not_found``（调用方据此判定未就绪）
- ``get_eval_build_status`` 返回 ``not_found``
- ``destroy_eval_env`` 返回失败原因，best-effort（不抛跨实现异常，避免与
  corp 侧 ``DefaultEnvBotServiceError`` 类不在同一处定义时的 except 失配）

旧 ``NoopEvalEnvLifecycle``（``EvalEnvLifecycleProtocol``）保留不动。
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.plugin_api.eval_env import EvalEnvBuildLifecycle
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.NOOP,
    rationale="评测环境离线：构建生命周期降级为 not_found / 失败",
)
class NoopEvalEnvBuildLifecycle(MockSeam, EvalEnvBuildLifecycle):
    """评测环境构建生命周期的 Noop 实现（评测功能关闭）。"""

    async def create_eval_env(
        self,
        *,
        bot_id: str,
        default_tag: str = "eval",
        operator_id: str = "",
        ttl_minutes: int | None = None,
    ) -> dict[str, Any]:
        return {"build_status": "not_found", "build_info": "评测环境功能已关闭"}

    async def get_eval_build_status(
        self,
        *,
        bot_id: str,
        default_tag: str = "eval",
    ) -> dict[str, Any]:
        return {"build_status": "not_found"}

    async def destroy_eval_env(
        self,
        *,
        bot_id: str,
        default_tag: str = "eval",
        reason: str = "",
    ) -> dict[str, Any]:
        return {"success": False, "reason": "评测环境功能已关闭"}
