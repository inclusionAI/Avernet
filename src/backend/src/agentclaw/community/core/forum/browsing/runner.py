"""BbsBrowseLoopRunner — push a one-shot Browse message to a Bot.

Single delivery seam shared by:
* framework cron (mode=framework): APScheduler-driven `*/30` tick → push once.
* self-cron (mode=openclaw): backend pushes a "register/remove local cron"
  event on subscribe/unsubscribe, and a "go browse once now" message when the
  trigger-self endpoint is called.

Heavy Bot transport is delegated to the existing ``OpenApiBotPort`` (bound by
``task_persistence_module``); this module never touches devices/BCS directly.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from injector import inject

from agentclaw.community.core.errors import NotFound, ValidationError
from agentclaw.community.core.forum.browsing.messages import (
    browse_once_message,
    cron_event_message,
)
from agentclaw.community.core.forum.models import (
    BROWSE_MODE_FRAMEWORK,
    BROWSE_MODE_OPENCLAW,
    BrowseSubscriptionRecord,
)
from agentclaw.community.core.forum.service_protocol import ForumServiceProtocol
from agentclaw.community.core.task.task_runner.client.ports import (
    BotSendResult,
    OpenApiBotPort,
)
from agentclaw.community.di.profile import DeployProfile
from agentclaw.community.log import get_logger

logger = get_logger()

_DEFAULT_BACKEND_URL = "http://localhost:8888"
PushAction = Literal["framework", "openclaw"]


def _resolve_backend_url() -> str:
    """Env-aware self URL (``BACKEND_URL`` > ``SINGLEBOX_BACKEND_URL`` > local)."""
    url = os.environ.get("BACKEND_URL")
    if url:
        return url
    if os.environ.get("DEPLOY_PROFILE", "").strip().lower() == DeployProfile.SINGLEBOX.value:
        return os.environ.get("SINGLEBOX_BACKEND_URL", _DEFAULT_BACKEND_URL)
    return _DEFAULT_BACKEND_URL


class BbsBrowseLoopRunner:
    """Push Bbs Browse messages to a Bot via the Bot-caller port."""

    @inject
    def __init__(
        self,
        bot: OpenApiBotPort,
        service: ForumServiceProtocol,
    ) -> None:
        self._bot = bot
        self._service = service

    async def push_browse_once(
        self,
        *,
        bot_id: str,
        expected_mode: str,
    ) -> dict[str, Any]:
        """Fire-and-forget: send one "go run bbs-browse now" message to ``bot_id``.

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
        backend_url = _resolve_backend_url()
        text = browse_once_message(
            bot_id=bot_id,
            backend_base_url=backend_url,
            subscription=subscription,
        )
        logger.info(
            "[bbs-browse-loop] push_browse_once bot=%s mode=%s backend=%s",
            bot_id, subscription.mode, backend_url,
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
        logger.info(
            "[bbs-browse-loop] cron_event bot=%s action=%s", bot_id, action
        )
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
