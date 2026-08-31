"""BaaS restart lifecycle hook for AICoding authorization refresh."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentclaw.community.core.bot_management.engines import resolve_provisioning
from agentclaw.community.core.events.bus import get_event_bus
from agentclaw.community.core.events.types import BaasPublishCompletedEvent
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.services.template_service import (
        TemplateService,
    )
    from agentclaw.community.core.repository.protocols.bot import BotRepository
    from agentclaw.community.core.skill_center.runtime_projection_contract import (
        BotRuntimeProjectorProtocol,
    )

logger = get_logger()


class AicodingRestartAuthorizationBaasPublishListener(LifecycleBase):
    """Delay AICoding restart authorization resync until BaaS restart publish completes.

    AICoding persists its restart resync intent in template_config. The listener
    re-reads current bot/template state after BaaS restart publish completion,
    then delegates the actual opt-in and refresh work back to the provisioning
    strategy so the BaaS restart flow does not hardcode AICoding details.
    """

    def __init__(
        self,
        *,
        bot_repo: "BotRepository",
        template_service: "TemplateService",
        skill_set_factory: Any = None,
        runtime_reconciler: BotRuntimeProjectorProtocol | None = None,
    ) -> None:
        self._bot_repo = bot_repo
        self._template_service = template_service
        self._skill_set_factory = skill_set_factory
        self._runtime_reconciler = runtime_reconciler

    async def startup(self) -> None:
        bus = get_event_bus()
        if bus.is_subscribed(BaasPublishCompletedEvent, self.handle):
            logger.info(
                "[aicoding.restart_authorization_listener] already subscribed to "
                "BaasPublishCompletedEvent"
            )
            return
        bus.subscribe(BaasPublishCompletedEvent, self.handle)
        logger.info(
            "[aicoding.restart_authorization_listener] subscribed to BaasPublishCompletedEvent"
        )

    def handle(self, event: BaasPublishCompletedEvent) -> None:
        if event.publish_kind != "restart":
            return

        try:
            bot = self._bot_repo.get_by_id_and_owner(event.bot_id, event.owner_id)
        except Exception as exc:
            logger.warning(
                "[aicoding.restart_authorization_listener] failed to read bot: "
                "bot_id=%s owner_id=%s publish_id=%s error=%s",
                event.bot_id,
                event.owner_id,
                event.publish_id,
                exc,
                exc_info=True,
            )
            return
        if not isinstance(bot, dict):
            return
        if str(bot.get("binding_id") or "") != str(event.binding_id):
            logger.info(
                "[aicoding.restart_authorization_listener] skip stale restart publish: "
                "bot_id=%s event_binding=%s bot_binding=%s publish_id=%s",
                event.bot_id,
                event.binding_id,
                bot.get("binding_id"),
                event.publish_id,
            )
            return

        try:
            template_config = self._template_service.get_template_config(event.bot_id)
        except Exception as exc:
            logger.warning(
                "[aicoding.restart_authorization_listener] failed to read template config: "
                "bot_id=%s publish_id=%s error=%s",
                event.bot_id,
                event.publish_id,
                exc,
                exc_info=True,
            )
            template_config = None

        try:
            ctx, strategy = resolve_provisioning(
                bot_id=event.bot_id,
                owner_id=str(bot.get("owner_id") or event.owner_id),
                bot_type=str(bot.get("bot_type") or ""),
                active_engine=bot.get("active_engine"),
                template_type=bot.get("template_type"),
                template_config=template_config if isinstance(template_config, dict) else None,
            )
            opted_in = strategy.refresh_restart_authorization(
                ctx,
                bot,
                None,
                skill_set_factory=self._skill_set_factory,
                runtime_reconciler=self._runtime_reconciler,
                template_service=self._template_service,
            )
            logger.info(
                "[aicoding.restart_authorization_listener] handled BaaS restart publish: "
                "bot_id=%s publish_id=%s strategy=%s opted_in=%s",
                event.bot_id,
                event.publish_id,
                strategy.engine_type,
                opted_in,
            )
        except Exception as exc:
            logger.warning(
                "[aicoding.restart_authorization_listener] strategy dispatch failed; "
                "bot_id=%s publish_id=%s error=%s",
                event.bot_id,
                event.publish_id,
                exc,
                exc_info=True,
            )
