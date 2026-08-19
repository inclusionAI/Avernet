"""NoopEvalVersionSync — 评测版本同步 Noop 实现。

评测功能关闭时：
- check_version 返回 True（不阻止）
- sync_version 为空操作
- on_publish_event 为空操作
"""

from __future__ import annotations

from typing import Any

from agentclaw.community.plugin_api.eval_env import EvalVersionSyncProtocol
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.NOOP,
    rationale="评测环境离线：版本检查不阻止，同步/事件为空操作",
)
class NoopEvalVersionSync(MockSeam, EvalVersionSyncProtocol):
    """评测版本同步的 Noop 实现。

    - ``check_version`` 返回 ``True``，不阻止任何版本检查
    - ``sync_version`` / ``on_publish_event`` 为空操作
    等效于跳过评测版本同步。
    """

    def check_version(
        self,
        *,
        bot_id: str,
        version: str,
    ) -> bool:
        """Noop：始终返回 True，不阻止。"""
        return True

    def sync_version(
        self,
        *,
        bot_id: str,
        version: str,
    ) -> None:
        """Noop：空操作。"""

    def on_publish_event(
        self,
        *,
        bot_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Noop：空操作。"""