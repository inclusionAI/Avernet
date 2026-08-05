"""Persist a draft restart's default-image policy before Device activation."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.bot_management.services.teclaw_provision_service import (
    DEFAULT_TECLAW_ENGINE_TYPES,
)
from agentclaw.community.core.devices.repository.protocol import (
    DeviceBindingRepository,
)
from agentclaw.community.core.events.types import DeviceAliveEvent
from agentclaw.community.core.service_bot.repository.bot_publish_repository import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.services.arka_image_pin import (
    persist_default_image_policy,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger

logger = get_logger()

IMAGE_POLICY_ON_ACTIVE_KEY = "image_policy_on_active"
DEFAULT_IMAGE_POLICY_VALUE = "default"


class DefaultImagePolicyActivationListener(LifecycleBase):
    """Finalize DEFAULT before activating recreate allocations carrying the intent."""

    @inject
    def __init__(
        self,
        bot_repository: BotRepository,
        publish_repository: BotPublishRepositoryProtocol,
        binding_repository: DeviceBindingRepository,
    ) -> None:
        self._bot_repository = bot_repository
        self._publish_repository = publish_repository
        self._binding_repository = binding_repository

    async def startup(self) -> None:
        from agentclaw.community.core.events.bus import get_event_bus

        bus = get_event_bus()
        existing = bus._handlers.get(DeviceAliveEvent, [])  # type: ignore[attr-defined]
        if self.handle in existing:
            return
        bus.subscribe(DeviceAliveEvent, self.handle, required=True)

    def handle(self, event: DeviceAliveEvent) -> None:
        # This policy belongs only to the ARCA destroy/recreate path. BaaS
        # in-place restart is finalized by its publish poll handler.
        if event.device_provider != "arca":
            return

        binding = self._binding_repository.get_by_id(event.binding_id)
        if binding is None:
            return
        props = getattr(binding, "device_props", None) or {}
        if props.get(IMAGE_POLICY_ON_ACTIVE_KEY) != DEFAULT_IMAGE_POLICY_VALUE:
            return

        bot = self._bot_repository.get_by_binding_id(event.binding_id)
        if not isinstance(bot, dict):
            # apply_device() starts the runtime before BotService writes the new
            # binding_id back to ac_bots. Keep the Binding PENDING so the next
            # Alive callback retries after that mapping becomes visible.
            raise RuntimeError(
                "service Bot mapping is not ready for default-image intent: "
                f"binding_id={event.binding_id}"
            )
        if bot.get("bot_type") != "service":
            return
        active_engine = str(bot.get("active_engine") or "").strip().lower()
        if active_engine in DEFAULT_TECLAW_ENGINE_TYPES:
            return

        bot_id = str(bot.get("bot_id") or "")
        owner_id = str(bot.get("owner_id") or "")
        if not bot_id or not owner_id:
            return

        persist_default_image_policy(
            bot_repository=self._bot_repository,
            publish_repository=self._publish_repository,
            bot_id=bot_id,
            owner_id=owner_id,
            env=str(getattr(binding, "env", None) or ""),
        )
        # Clear only after both Bot and Draft have accepted DEFAULT. A failed
        # persistence leaves the intent durable for diagnosis/retry.
        self._binding_repository.update_device_props(
            binding_id=event.binding_id,
            props={IMAGE_POLICY_ON_ACTIVE_KEY: None},
        )
        logger.info(
            "[default_image_policy_listener] persisted DEFAULT before activation: "
            "bot_id=%s binding_id=%s",
            bot_id,
            event.binding_id,
        )
