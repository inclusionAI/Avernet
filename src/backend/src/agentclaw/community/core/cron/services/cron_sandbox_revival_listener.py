"""Event listener: 新沙箱激活时撤销 binding 的"沙箱已销毁"判决。

背景（PR #1635 评审 P0）：CronRuntimeTargetMixin 的 binding 级负缓存（60 s）
会跳过最近被判"沙箱已销毁"的 binding。一次重启/republish 里，销毁 gap 内
的轮询可能先把 binding 打上死亡判决，随后新沙箱在同一 binding 上激活并触发
DeviceActivatedEvent；若判决不清除，``cron_auto_setup`` 的同名幂等检查
（只读 ``list_all_crons`` 的 ``data``，感知不到 failed_targets/skip）会在活
沙箱上重复创建 autoInitiate 任务。

"沙箱激活"正是死亡判决的反证：订阅 ``DeviceActivatedEvent``，事件到达即调
``CronRelayService.clear_sandbox_down_verdict``。组件通过 DI 绑定 +
``discover_lifecycle_participants`` 参与 startup，模式与
``CronAutoSetupListener`` 一致。
"""
from __future__ import annotations

from injector import inject

from agentclaw.community.core.cron.services.cron_relay import CronRelayService
from agentclaw.community.core.events.types import DeviceActivatedEvent
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger

logger = get_logger()


class CronSandboxRevivalListener(LifecycleBase):
    """DeviceActivatedEvent listener that revives a binding's cron queries."""

    @inject
    def __init__(self, cron_relay: CronRelayService) -> None:
        # Concrete class on purpose: the injector resolves CronRelayService via
        # its cron_module self-binding (same style as CronRelayService's own
        # constructor params).
        self._cron_relay = cron_relay

    async def startup(self) -> None:
        """Lifecycle hook — subscribe ``self._handle`` to DeviceActivatedEvent.

        Idempotent: re-runs are safe (membership check before subscribe).
        """
        from agentclaw.community.core.events.bus import get_event_bus

        bus = get_event_bus()
        existing = bus._handlers.get(DeviceActivatedEvent, [])  # type: ignore[attr-defined]
        if self._handle in existing:
            logger.info(
                "[cron_sandbox_revival_listener] already subscribed to "
                "DeviceActivatedEvent"
            )
            return
        bus.subscribe(DeviceActivatedEvent, self._handle)
        logger.info("[cron_sandbox_revival_listener] subscribed to DeviceActivatedEvent")

    def _handle(self, event: DeviceActivatedEvent) -> None:
        """DeviceActivatedEvent 事件处理器：撤销该 binding 的死亡判决。"""
        try:
            cleared = self._cron_relay.clear_sandbox_down_verdict(event.binding_id)
            if cleared:
                logger.info(
                    "[cron_sandbox_revival_listener] sandbox revived: cleared "
                    "destroyed-sandbox verdict for binding_id=%s (device_id=%s)",
                    event.binding_id,
                    event.device_id,
                )
            else:
                logger.debug(
                    "[cron_sandbox_revival_listener] activation for binding_id=%s "
                    "had no verdict to clear",
                    event.binding_id,
                )
        except Exception as e:
            # 一个 handler 不允许拖垮事件总线循环；判决若真的没清掉，
            # 60 s TTL 本身就是自愈兜底，这里只告警。
            logger.warning(
                "[cron_sandbox_revival_listener] failed to clear verdict for "
                "binding_id=%s: %s",
                event.binding_id,
                e,
            )
