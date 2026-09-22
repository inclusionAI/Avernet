"""Event listener: 自动为 is_hosted_24x7 的 7×24 托管 Coding Bot 创建定时任务。

订阅 DeviceActivatedEvent，在设备激活后按 per-engine 能力位
（``supports_auto_cron_setup``，经引擎策略 registry 按 ``active_engine`` 解析，
未注册引擎保守拒绝；能力位内含模板级守卫——aicoding/claude_code 仅放行
非空且非 legacy ``normalCC`` 的 ``template_type``）放行，交由
CronAutoSetupService 读取 template ext 判定 ``is_hosted_24x7 == 1``
（不再以 template_type=applicationCoding 为必要条件，模板工厂 bot
如 mcptestpq 同样走此链路）。
"""
from __future__ import annotations

import asyncio

from injector import inject

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_management.engines import supports_auto_cron_setup
from agentclaw.community.core.cron.services.aicoding.cron_auto_setup import CronAutoSetupService
from agentclaw.community.core.events.types import DeviceActivatedEvent
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger

logger = get_logger()


class CronAutoSetupListener(LifecycleBase):
    """DeviceActivatedEvent listener that auto-creates 7×24 cron tasks for aicoding-engine bots.

    Subscribes ``self._handle`` to the event bus in ``startup()``.
    Replaces the bare ``handle_device_activated_for_cron`` function form
    so the listener can participate in the standard Lifecycle dispatch.
    """

    @inject
    def __init__(
        self,
        bot_repo: BotRepository,
        cron_auto_setup_service: CronAutoSetupService,
    ) -> None:
        self._bot_repo = bot_repo
        self._cron_auto_setup_service = cron_auto_setup_service

    async def startup(self) -> None:
        """Lifecycle hook — subscribe ``self._handle`` to DeviceActivatedEvent.

        Idempotent: re-runs are safe (membership check before subscribe).
        """
        from agentclaw.community.core.events.bus import get_event_bus

        bus = get_event_bus()
        existing = bus._handlers.get(DeviceActivatedEvent, [])  # type: ignore[attr-defined]
        if self._handle in existing:
            logger.info(
                "[cron_auto_setup_listener] already subscribed to DeviceActivatedEvent"
            )
            return
        bus.subscribe(DeviceActivatedEvent, self._handle)
        logger.info(
            "[cron_auto_setup_listener] subscribed to DeviceActivatedEvent"
        )

    def _handle(self, event: DeviceActivatedEvent) -> None:
        """DeviceActivatedEvent 事件处理器：自动创建 7×24 托管定时任务。"""
        logger.info(
            "[cron_auto_setup_listener] received DeviceActivatedEvent: "
            "device_id=%s binding_id=%s provider=%s",
            event.device_id,
            event.binding_id,
            event.device_provider,
        )

        try:
            # 1. 通过 binding_id 查找关联的 Bot
            bot = self._bot_repo.get_by_binding_id(event.binding_id)
            if bot is None:
                logger.info(
                    "[cron_auto_setup_listener] no bot bound to binding_id=%s, skipping",
                    event.binding_id,
                )
                return

            bot_id = bot.get("bot_id")
            owner_id = bot.get("owner_id")
            owner_name = bot.get("owner_name") or owner_id
            if not bot_id or not owner_id:
                logger.info(
                    "[cron_auto_setup_listener] bot missing bot_id or owner_id "
                    "(binding_id=%s), skipping",
                    event.binding_id,
                )
                return

            # 2. 检查引擎是否声明 7×24 自动 cron 能力位（含模板级守卫：
            #    aicoding/claude_code 仅放行非空且非 normalCC 的 template_type；
            #    is_hosted_24x7 / dima_space_id 在 template ext 里，由 service
            #    读取后判定）
            active_engine = bot.get("active_engine", "")
            template_type = bot.get("template_type", "")
            engine_eligible = supports_auto_cron_setup(
                engine_type=active_engine,
                template_type=template_type,
            )
            logger.info(
                "[cron_auto_setup_listener] bot %s check: engine=%s, "
                "template_type=%s, auto_cron_eligible=%s",
                bot_id, active_engine, template_type, engine_eligible,
            )
            if not engine_eligible:
                logger.info(
                    "[cron_auto_setup_listener] bot %s engine=%s "
                    "template_type=%s does not support auto-cron setup, skipping",
                    bot_id, active_engine, template_type,
                )
                return

            # 3. 异步执行 cron 自动创建
            logger.info(
                "[cron_auto_setup_listener] bot %s engine supports auto-cron setup, "
                "triggering cron setup",
                bot_id,
            )
            self._schedule_cron_setup(bot_id, owner_id, owner_name)

        except Exception as exc:
            logger.exception(
                "[cron_auto_setup_listener] handler failed: device_id=%s exc=%s",
                event.device_id,
                exc,
            )

    def _schedule_cron_setup(self, bot_id: str, owner_id: str, owner_name: str) -> None:
        """在事件循环中异步执行 cron 自动创建。"""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self._async_cron_setup(bot_id, owner_id, owner_name)
            )
            logger.info(
                "[cron_auto_setup_listener] Scheduled cron setup task for bot %s",
                bot_id,
            )
        except RuntimeError:
            logger.info(
                "[cron_auto_setup_listener] No running event loop, creating new one for bot %s",
                bot_id,
            )
            try:
                asyncio.run(self._async_cron_setup(bot_id, owner_id, owner_name))
            except Exception as e:
                logger.error(
                    "[cron_auto_setup_listener] asyncio.run failed for bot %s: %s",
                    bot_id, e, exc_info=True,
                )

    async def _async_cron_setup(self, bot_id: str, owner_id: str, owner_name: str) -> None:
        """异步执行 cron 自动创建的核心逻辑。"""
        try:
            result = await self._cron_auto_setup_service.auto_setup_cron_for_bot(
                bot_id=bot_id,
                owner_id=owner_id,
                nick_name=owner_name,
            )
            if result:
                logger.info(
                    "[cron_auto_setup_listener] Cron auto-setup completed for bot %s",
                    bot_id,
                )
            else:
                logger.info(
                    "[cron_auto_setup_listener] Cron auto-setup skipped for bot %s",
                    bot_id,
                )
        except Exception as e:
            logger.error(
                "[cron_auto_setup_listener] Cron auto-setup failed for bot %s: %s",
                bot_id, e, exc_info=True,
            )
