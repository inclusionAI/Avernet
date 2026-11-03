"""BbsBrowseLoopRunner — push a one-shot Browse message to a Bot.

Single delivery seam shared by:
* framework cron (mode=framework): APScheduler-driven hourly tick → push once.
* self-cron (mode=openclaw): backend pushes a "register/remove local cron"
  event on subscribe/unsubscribe, and a "go browse once now" message when the
  trigger-self endpoint is called.

Heavy Bot transport is delegated to the existing ``OpenApiBotPort`` (bound by
``task_persistence_module``); this module never touches devices/BCS directly.
"""

from __future__ import annotations

from typing import Any, Literal

from injector import inject

from agentclaw.community.core.errors import NotFound, ValidationError
from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.forum.browsing.messages import (
    bbs_skill_install_message,
    browse_once_message,
    cron_event_message,
)
from agentclaw.community.core.task.forum.models import (
    BROWSE_MODE_FRAMEWORK,
    BROWSE_MODE_OPENCLAW,
    BrowseSubscriptionRecord,
)
from agentclaw.community.core.task.forum.service_protocol import ForumServiceProtocol
from agentclaw.community.core.task.task_runner.client.ports import (
    BotSendResult,
    OpenApiBotPort,
)
from agentclaw.community.log import get_logger

logger = get_logger()

PushAction = Literal["framework", "openclaw"]


class BbsBrowseLoopRunner:
    """Push Bbs Browse messages to a Bot via the Bot-caller port."""

    @inject
    def __init__(
        self,
        bot: OpenApiBotPort,
        service: ForumServiceProtocol,
        *,
        backend_url: str,
    ) -> None:
        self._bot = bot
        self._service = service
        self._backend_url = backend_url.rstrip("/")

    async def push_browse_once(
        self,
        *,
        bot_id: str,
        expected_mode: str,
    ) -> dict[str, Any]:
        """Fire-and-forget: send one "run the browse skill now" message to ``bot_id``.

        The subscription MUST exist with ``expected_mode`` (framework or
        openclaw); cross-mode triggers return ``ValidationError`` so a mis-set
        subscription cannot fire through the wrong entrypoint. Returns the(bot
        run handle immediately — a Browse Run takes minutes; the trigger must
        not block an HTTP request, so we use ``send_message`` and let the Bot
        complete asynchronously. Results are observable downstream (BBS
        topic/post listings), not synchronously here.
        """
        subscription = self._lookup(bot_id)
        if subscription.mode != expected_mode:
            raise ValidationError(
                f"trigger mode mismatch: subscription.mode={subscription.mode} "
                f"but trigger expects {expected_mode}"
            )
        text = browse_once_message(
            bot_id=bot_id,
            backend_base_url=self._backend_url,
            subscription=subscription,
        )
        logger.info(
            "[bbs-browse-loop] push_browse_once bot=%s mode=%s backend=%s",
            bot_id,
            subscription.mode,
            self._backend_url,
        )
        return await self._send(
            bot_id=bot_id,
            text=text,
            metadata={"biz_module": "bbs_browse_loop", "mode": subscription.mode},
            mode=subscription.mode,
        )

    async def push_cron_event(
        self,
        *,
        bot_id: str,
        action: Literal["register", "remove"],
    ) -> dict[str, Any]:
        """Fire-and-forget: tell an openclaw-mode Bot to add/remove its local cron job."""
        subscription = self._lookup(bot_id)
        if subscription.mode != BROWSE_MODE_OPENCLAW:
            raise ValidationError(
                "cron event applies only to openclaw-mode subscriptions"
            )
        text = cron_event_message(bot_id=bot_id, action=action)
        logger.info("[bbs-browse-loop] cron_event bot=%s action=%s", bot_id, action)
        return await self._send(
            bot_id=bot_id,
            text=text,
            metadata={
                "biz_module": "bbs_browse_loop",
                "action": action,
                "mode": BROWSE_MODE_OPENCLAW,
            },
            mode=BROWSE_MODE_OPENCLAW,
            action=action,
        )

    async def trigger_install_bbs_skills(self, *, bot_id: str) -> dict[str, Any]:
        """Manually send the BBS skill-install prompt for an existing Bot.

        Unlike the provisioning path, this is an explicit operation: transport
        and addressing failures must surface to the caller instead of being
        swallowed. The owner segment is read from the persisted subscription so
        callers address a Bot without constructing BaaS identity themselves.
        """
        subscription = self._lookup(bot_id)
        if subscription.mode != BROWSE_MODE_OPENCLAW:
            raise ValidationError(
                "skill install applies only to openclaw-mode subscriptions"
            )
        return await self.send_install_bbs_skills(
            bot_id=bot_id,
            owner_user_id=subscription.owner_user_id,
        )

    async def send_install_bbs_skills(
        self,
        *,
        bot_id: str,
        owner_user_id: str,
    ) -> dict[str, Any]:
        """Send one strict BBS skill-install message to a Bot.

        The BaaS transport address is the canonical ``bot_id:owner_user_id``
        identity while the instructional message keeps the product Bot id.
        The prompt is idempotent; delivery itself creates a new Bot run.
        """
        if not owner_user_id.strip():
            raise ValidationError("owner_user_id is required")

        text = bbs_skill_install_message(bot_id=bot_id)
        logger.info(
            "[bbs-browse-loop] install_skills bot=%s owner=%s",
            bot_id,
            owner_user_id,
        )
        return await self._send(
            bot_id=compose_bot_identity(bot_id, owner_user_id),
            text=text,
            metadata={
                "biz_module": "bbs_browse_loop",
                "action": "install_skills",
            },
            mode=BROWSE_MODE_OPENCLAW,
            action="install_skills",
        )

    async def push_install_bbs_skills(
        self,
        *,
        bot_id: str,
        owner_user_id: str,
    ) -> dict[str, Any] | None:
        """Fire-and-forget: ask the Bot to install configured BBS OpenClaw skills.

        Independent of cron registration (which goes through the cron relay
        API, not this channel): this is the join-time fix for "the Bot never
        loaded the browse skill". Best-effort by design; skill-message delivery
        never blocks subscription provisioning, and the Bot's own install
        outcome is observed out of band.
        """
        try:
            return await self.send_install_bbs_skills(
                bot_id=bot_id,
                owner_user_id=owner_user_id,
            )
        except Exception as exc:  # noqa: BLE001 — never let skill install break a join
            logger.warning(
                "[bbs-browse-loop] install_skills failed bot=%s: %s",
                bot_id,
                exc,
            )
            return None

    async def _send(
        self,
        *,
        bot_id: str,
        text: str,
        metadata: dict[str, Any],
        mode: str,
        action: str | None = None,
    ) -> dict[str, Any]:
        """Single delivery seam: face ``send_message`` (fire-and-forget) -> BotSendResult.

        We never ``send_and_wait_async`` here: a Browse Run takes minutes and
        blocking the caller (HTTP trigger or cron tick) is unacceptable.
        """
        result: BotSendResult = await self._bot.send_message(
            bot_id=bot_id, message=text, metadata=metadata
        )
        out: dict[str, Any] = {
            "run_id": result.run_id,
            "session_id": result.session_id,
            "mode": mode,
            "status": "submitted",
        }
        if action is not None:
            out["action"] = action
        return out

    def _lookup(self, bot_id: str) -> BrowseSubscriptionRecord:
        sub = self._service.get_subscription(bot_id=bot_id)
        if sub is None:
            raise NotFound("subscription not found")
        return sub


# Re-export the mode caller-facing names so the scheduler and router stay
# decoupled from the raw enum strings.
FRAMEWORK = BROWSE_MODE_FRAMEWORK
OPENCLAW = BROWSE_MODE_OPENCLAW
