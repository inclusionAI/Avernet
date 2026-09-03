"""Event listener: full runtime projection when a runtime becomes ready.

Subscribed to ``DeviceActivatedEvent`` for first activation and to
``RuntimeProjectionRequestedEvent`` for successful BaaS restarts. Both paths
refresh runtime state from the DB's current desired state.

The listener is constructed via the DI injector at app startup; its
``handle`` method is bound to the event bus so the bus can dispatch with
just the event payload.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.events.types import (
    DeviceActivatedEvent,
    RuntimeProjectionRequestedEvent,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )
    from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
    from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher
logger = get_logger()

_TRANSITION_AUTHORITY = "transition"
_RuntimeReadyEvent = DeviceActivatedEvent | RuntimeProjectionRequestedEvent


def _run_reconcile_blocking(coro: Any) -> Any:
    """Run an async reconcile while synchronously handling a lifecycle event."""
    from agentclaw.community.utils.avernet_tenant import bind_current_avernet_tenant

    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["result"] = asyncio.run(coro)
        except BaseException as error:  # noqa: BLE001 - preserve failure semantics
            box["error"] = error

    thread = threading.Thread(target=bind_current_avernet_tenant(runner), daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("result")


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
        desktop_layout_authority: Callable[[dict], str | None] | None = None,
        desktop_reconcile_wakeup: (
            Callable[[DeviceActivatedEvent], None] | None
        ) = None,
        runtime_reconcile: Callable[[str, str], object] | None = None,
        runtime_non_skill_reconcile: Callable[[str, str], object] | None = None,
    ) -> None:
        self._bot_repo = bot_repo
        self._skill_set_factory = skill_set_factory
        self._resolver = resolver
        self._device_sync_dispatcher = device_sync_dispatcher
        self._desktop_layout_authority = desktop_layout_authority
        self._desktop_reconcile_wakeup = desktop_reconcile_wakeup
        self._runtime_reconcile = runtime_reconcile
        self._runtime_non_skill_reconcile = runtime_non_skill_reconcile

    async def startup(self) -> None:
        """Subscribe ``self.handle`` to activation and reprojection events.

        Replaces the explicit wiring in the pre-R11
        ``_register_event_listeners()`` helper. Idempotent — checks
        membership in the bus's handler list before subscribing, so a
        repeat boot in a hot-reload context doesn't double-subscribe.
        """
        from agentclaw.community.core.events.bus import get_event_bus

        bus = get_event_bus()
        for event_type in (DeviceActivatedEvent, RuntimeProjectionRequestedEvent):
            existing = bus._handlers.get(event_type, [])  # type: ignore[attr-defined]
            if self.handle in existing:
                logger.info(
                    "[skill_symlink_listener] already subscribed to %s",
                    event_type.__name__,
                )
                continue
            bus.subscribe(event_type, self.handle)
            logger.info(
                "[skill_symlink_listener] subscribed to %s", event_type.__name__
            )

    def handle(self, event: _RuntimeReadyEvent) -> None:
        logger.info(
            "[skill_symlink_listener] received %s: "
            "device_id=%s binding_id=%s provider=%s sandbox_id=%s",
            type(event).__name__,
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

            is_desktop = bot.get("bot_type") == "desktop"
            if isinstance(event, DeviceActivatedEvent):
                self._enqueue_desktop_reconciliation(
                    event=event,
                    is_desktop=is_desktop,
                )
            initial_authority = self._resolve_desktop_layout_authority(bot)
            ctx = self._resolver.resolve_for_bot(bot_id, owner_id)
            if ctx.binding_id != event.binding_id:
                logger.info(
                    "[skill_symlink_listener] activated binding is no longer "
                    "current, skipping: bot_id=%s event_binding_id=%s "
                    "current_binding_id=%s",
                    bot_id,
                    event.binding_id,
                    ctx.binding_id,
                )
                return

            if initial_authority == _TRANSITION_AUTHORITY:
                logger.info(
                    "[skill_symlink_listener] Desktop transitional mapping "
                    "is owned by durable reconciliation: bot_id=%s",
                    bot_id,
                )
                if self._runtime_non_skill_reconcile is not None:
                    outcome = self._runtime_non_skill_reconcile(
                        str(bot_id), str(owner_id)
                    )
                    if asyncio.iscoroutine(outcome):
                        _run_reconcile_blocking(outcome)
                return

            if self._runtime_reconcile is not None:
                # Both first activation and explicit post-restart projection
                # rebuild the complete DB desired state through the same
                # Reconciler as explicit mutations; do not rebuild a legacy
                # Default-exclusion mapping in this listener.
                try:
                    outcome = self._runtime_reconcile(str(bot_id), str(owner_id))
                    if asyncio.iscoroutine(outcome):
                        _run_reconcile_blocking(outcome)
                finally:
                    if isinstance(event, DeviceActivatedEvent):
                        self._reenqueue_if_desktop_cutover_started(
                            event=event,
                            bot=bot if is_desktop else None,
                            initial_authority=initial_authority,
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
            try:
                result = device_sync.sync_symlinks(symlinks)
            finally:
                if isinstance(event, DeviceActivatedEvent):
                    self._reenqueue_if_desktop_cutover_started(
                        event=event,
                        bot=bot if is_desktop else None,
                        initial_authority=initial_authority,
                    )
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

    def _resolve_desktop_layout_authority(self, bot: dict) -> str | None:
        if (
            bot.get("bot_type") != "desktop"
            or self._desktop_layout_authority is None
        ):
            return None
        try:
            return self._desktop_layout_authority(bot)
        except Exception:
            logger.exception(
                "[skill_symlink_listener] Desktop layout lookup failed; "
                "preserving Legacy wake behavior: bot_id=%s",
                bot.get("bot_id"),
            )
            return None

    def _enqueue_desktop_reconciliation(
        self,
        *,
        event: DeviceActivatedEvent,
        is_desktop: bool,
    ) -> None:
        """Wake P3 from the existing Desktop activation callback.

        Keeping the wake inside this already-subscribed listener avoids adding
        a second required DeviceActivated subscriber for non-Desktop bots.  A
        wake failure must never suppress the existing Legacy mapping refresh.
        """

        if not is_desktop or self._desktop_reconcile_wakeup is None:
            return
        try:
            self._desktop_reconcile_wakeup(event)
        except Exception:
            logger.exception(
                "[skill_symlink_listener] Desktop reconciliation wake failed; "
                "continuing Legacy mapping refresh: binding_id=%s",
                event.binding_id,
            )

    def _reenqueue_if_desktop_cutover_started(
        self,
        *,
        event: DeviceActivatedEvent,
        bot: dict | None,
        initial_authority: str | None,
    ) -> None:
        if (
            bot is None
            or self._desktop_layout_authority is None
            or self._desktop_reconcile_wakeup is None
        ):
            return
        current_authority = self._resolve_desktop_layout_authority(bot)
        if current_authority is None or current_authority == initial_authority:
            return
        logger.info(
            "[skill_symlink_listener] Desktop layout authority changed during "
            "mapping sync; enqueueing durable convergence: bot_id=%s "
            "initial_authority=%s authority=%s",
            bot.get("bot_id"),
            initial_authority,
            current_authority,
        )
        self._desktop_reconcile_wakeup(event)
