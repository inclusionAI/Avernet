"""Shared Core operation for recycling a personal managed-cloud Bot."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.bot_dormant.protocols import BotServiceProtocol
from agentclaw.community.core.bot_dormant.types import BotLifecycleResult
from agentclaw.community.core.bot_management.services.bot_service import (
    BotInvalidLifecycleStateError,
    BotNotFoundError,
    BotOperationNotAllowedError,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.passport import PassportPlugin


logger = get_logger()


class RecycleReleaseFailed(RuntimeError):
    """The Bot resource could not be confirmed released."""


class RecycleBotService:
    """Release one personal Bot resource and mark the Bot as recycled."""

    @inject
    def __init__(
        self,
        bot_service: BotServiceProtocol,
        passport_plugin: PassportPlugin,
    ) -> None:
        self._bot_service = bot_service
        self._passport = passport_plugin

    def recycle(
        self,
        *,
        bot_id: str,
        owner_id: str,
        owner_name: str | None = None,
    ) -> BotLifecycleResult:
        bot = self._bot_service.get_bot(bot_id=bot_id, user_id=owner_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        self.ensure_supported(bot)

        status = str(bot.get("status") or "")
        if status == "RECYCLED":
            return BotLifecycleResult(
                bot_id=bot_id,
                owner_id=owner_id,
                status="RECYCLED",
                changed=False,
            )
        if status != "ACTIVE":
            raise BotInvalidLifecycleStateError(
                bot_id=bot_id,
                current_status=status or "UNKNOWN",
            )

        stop_kwargs = {
            "bot_id": bot_id,
            "user_id": owner_id,
            "release_reason": "dormant_recycle",
        }
        if owner_name:
            stop_kwargs["nick_name"] = owner_name
        release_ok = self._bot_service.stop_bot(**stop_kwargs)
        if release_ok is False:
            raise RecycleReleaseFailed(
                f"stop_bot reported release failure for bot_id={bot_id}"
            )

        self._bot_service.update_status(
            bot_id=bot_id,
            user_id=owner_id,
            status="RECYCLED",
        )
        try:
            self._passport.freeze_agent_passport(
                bot_id=bot_id,
                owner_workno=owner_id,
                reason="dormant recycle",
            )
        except Exception as exc:
            logger.warning(
                "[RecycleBotService.recycle] passport freeze failed "
                "bot_id=%s owner_id=%s error=%s",
                bot_id,
                owner_id,
                exc,
            )

        return BotLifecycleResult(
            bot_id=bot_id,
            owner_id=owner_id,
            status="RECYCLED",
            changed=True,
        )

    def ensure_supported(self, bot: dict) -> None:
        """Reject Bot kinds outside the dormant lifecycle boundary."""
        if bot.get("bot_type") != "personal":
            raise BotOperationNotAllowedError(
                "dormant recycle only supports personal bots"
            )
        if self._bot_service.is_teclaw_bot(bot.get("active_engine")):
            raise BotOperationNotAllowedError(
                "teclaw bots use their engine-owned lifecycle"
            )
