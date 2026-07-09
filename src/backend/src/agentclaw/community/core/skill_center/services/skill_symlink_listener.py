"""Event listener: full skill-symlink sync when a device becomes active.

Subscribed to DeviceActivatedEvent. Triggered once per container ready
callback (new bot, restart, or post-batch-restart) — ensures that every
time a container comes up, its symlinks are refreshed from the DB's
current active skill sets (including default sets).

The listener is constructed via the DI injector at app startup; its
``handle`` method is bound to the event bus so the bus can dispatch with
just the event payload.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.events.types import DeviceActivatedEvent
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )
    from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
    from agentclaw.community.core.devices.services.device_sync_dispatcher import DeviceSyncDispatcher


logger = get_logger()


class SkillSymlinkListener(LifecycleBase):
    """Full symlink sync for the bot bound to an activated device.

    Constructor-injected so the handler doesn't reach back into the
    injector on every event dispatch.
    """

    def __init__(
        self,
        bot_repo: BotRepository,
        skill_set_factory: "SkillSetServiceFactory",
        resolver: "DeviceContextResolver",
        device_sync_dispatcher: "DeviceSyncDispatcher",
    ) -> None:
        self._bot_repo = bot_repo
        self._skill_set_factory = skill_set_factory
        self._resolver = resolver
        self._device_sync_dispatcher = device_sync_dispatcher

    async def startup(self) -> None:
        """Lifecycle hook — subscribe ``self.handle`` to DeviceActivatedEvent.

        Replaces the explicit wiring in the pre-R11
        ``_register_event_listeners()`` helper. Idempotent — checks
        membership in the bus's handler list before subscribing, so a
        repeat boot in a hot-reload context doesn't double-subscribe.
        """
        from agentclaw.community.core.events.bus import get_event_bus

        bus = get_event_bus()
        existing = bus._handlers.get(DeviceActivatedEvent, [])  # type: ignore[attr-defined]
        if self.handle in existing:
            logger.info(
                "[skill_symlink_listener] already subscribed to DeviceActivatedEvent"
            )
            return
        bus.subscribe(DeviceActivatedEvent, self.handle)
        logger.info(
            "[skill_symlink_listener] subscribed to DeviceActivatedEvent"
        )

    def handle(self, event: DeviceActivatedEvent) -> None:
        logger.info(
            "[skill_symlink_listener] received DeviceActivatedEvent: "
            "device_id=%s binding_id=%s provider=%s sandbox_id=%s",
            event.device_id,
            event.binding_id,
            event.device_provider,
            event.sandbox_id,
        )

        try:
            bot = self._bot_repo.get_by_binding_id(event.binding_id)
            if bot is None:
                logger.info(
                    "[skill_symlink_listener] no bot bound to binding_id=%s, skipping",
                    event.binding_id,
                )
                return

            bot_id = bot.get("bot_id")
            owner_id = bot.get("owner_id")
            if not bot_id or not owner_id:
                logger.info(
                    "[skill_symlink_listener] bot missing bot_id or owner_id "
                    "(binding_id=%s bot=%s), skipping",
                    event.binding_id,
                    bot,
                )
                return

            from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE

            service = self._skill_set_factory.create(
                user_id=owner_id,
                entity_id=owner_id,
                bot_id=bot_id,
                engine_type=bot.get("active_engine") or DEFAULT_ENGINE_TYPE,
            )
            mappings = service.get_symlink_mappings(user_id=owner_id, bolt_id=bot_id)
            symlinks = [sm.to_dict() for sm in mappings]

            ctx = self._resolver.resolve_for_bot(bot_id, owner_id)
            device_sync = self._device_sync_dispatcher.dispatch(ctx)
            logger.info(
                "[skill_symlink_listener] syncing symlinks: plugin=%s count=%d "
                "sandbox_id=%s bot_id=%s owner_id=%s",
                type(device_sync).__name__,
                len(symlinks),
                event.sandbox_id,
                bot_id,
                owner_id,
            )
            result = device_sync.sync_symlinks(symlinks)
            logger.info(
                "[skill_symlink_listener] sync result: success=%s message=%s",
                result.get("success"),
                result.get("message"),
            )
        except Exception as exc:
            logger.exception(
                "[skill_symlink_listener] handler failed: device_id=%s exc=%s",
                event.device_id,
                exc,
            )
