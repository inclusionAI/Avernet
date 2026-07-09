"""ActivateBotService — reactivate a RECYCLED bot.

Flow:
  1. Fetch bot, check state.
  2. REACTIVATING → friendly early return (idempotent).
  3. non-RECYCLED  → InvalidBotStateError.
  4. RECYCLED      → update_status(REACTIVATING) + spawn background thread
                      that calls passport unfreeze then start_bot;
                      on failure rolls back: passport freeze + RECYCLED.
"""
from __future__ import annotations

import threading

from injector import inject

from agentclaw.community.core.bot_dormant.protocols import BotServiceProtocol
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.passport import PassportPlugin


logger = get_logger()


class InvalidBotStateError(Exception):
    """Raised when activate is called on a bot that is not RECYCLED."""


class ActivateBotService:
    @inject
    def __init__(
        self,
        bot_service: BotServiceProtocol,
        passport_plugin: PassportPlugin,
    ):
        self._bot_service = bot_service
        self._passport = passport_plugin

    def activate(self, bot_id: str, user_id: str, nick_name: str | None = None) -> dict:
        """Activate a RECYCLED bot.

        Returns a dict with keys ``status`` and ``message``.
        Raises ``InvalidBotStateError`` if the bot is not RECYCLED (or REACTIVATING).
        """
        bot = self._bot_service.get_bot(bot_id=bot_id, user_id=user_id)
        if not bot:
            logger.warning(
                "[activate] bot not found bot_id=%s user_id=%s",
                bot_id, user_id,
            )
            raise InvalidBotStateError(f"bot not found: {bot_id}")

        status = bot.get("status")
        logger.info(
            "[activate] request bot_id=%s user_id=%s status=%s nick_name=%s",
            bot_id, user_id, status, nick_name,
        )

        # Idempotent: already in progress → friendly return without side-effects
        if status == "REACTIVATING":
            logger.info(
                "[activate] already reactivating bot_id=%s user_id=%s",
                bot_id, user_id,
            )
            return {"status": "REACTIVATING", "message": "激活中，请稍候"}

        if status != "RECYCLED":
            logger.warning(
                "[activate] invalid status bot_id=%s user_id=%s status=%s",
                bot_id, user_id, status,
            )
            raise InvalidBotStateError(
                f"only RECYCLED bot can be activated, current: {status}"
            )

        # Transition to REACTIVATING synchronously so the caller gets a loading state.
        logger.info(
            "[activate] update status to REACTIVATING bot_id=%s user_id=%s",
            bot_id, user_id,
        )
        self._bot_service.update_status(
            bot_id=bot_id, user_id=user_id, status="REACTIVATING"
        )

        # Launch background task: unfreeze passport → start_bot; rollback on failure.
        thread = threading.Thread(
            target=self._reactivate_async,
            args=(bot_id, user_id, nick_name or user_id),
            daemon=True,
        )
        thread.start()
        logger.info(
            "[activate] background reactivation dispatched bot_id=%s user_id=%s",
            bot_id, user_id,
        )
        return {"status": "REACTIVATING", "message": "激活中"}

    def _reactivate_async(self, bot_id: str, user_id: str, nick_name: str) -> None:
        """Background task: unfreeze passport then start the bot."""
        try:
            logger.info(
                "[activate] passport unfreeze start bot_id=%s user_id=%s",
                bot_id, user_id,
            )
            self._passport.unfreeze_agent_passport(
                bot_id=bot_id,
                owner_workno=user_id,
                reason="manual reactivate",
            )
            logger.info(
                "[activate] passport unfreeze success bot_id=%s user_id=%s",
                bot_id, user_id,
            )
        except Exception as e:
            logger.error(
                "[activate] passport unfreeze failed bot_id=%s: %s", bot_id, e
            )
            self._bot_service.update_status(
                bot_id=bot_id, user_id=user_id, status="RECYCLED"
            )
            return

        try:
            # start_bot internally calls apply_device; on success sets status=ACTIVE.
            logger.info(
                "[activate] start_bot start bot_id=%s user_id=%s nick_name=%s",
                bot_id, user_id, nick_name,
            )
            self._bot_service.start_bot(
                bot_id=bot_id, user_id=user_id, nick_name=nick_name
            )
            logger.info(
                "[activate] start_bot dispatched bot_id=%s user_id=%s",
                bot_id, user_id,
            )
        except Exception as e:
            logger.error(
                "[activate] start_bot failed bot_id=%s: %s", bot_id, e
            )
            # Best-effort rollback: re-freeze the passport.
            try:
                self._passport.freeze_agent_passport(
                    bot_id=bot_id,
                    owner_workno=user_id,
                    reason="reactivate rollback",
                )
                logger.info(
                    "[activate] passport rollback freeze success bot_id=%s user_id=%s",
                    bot_id, user_id,
                )
            except Exception as ferr:
                logger.error(
                    "[activate] passport rollback also failed bot_id=%s: %s",
                    bot_id,
                    ferr,
                )
            self._bot_service.update_status(
                bot_id=bot_id, user_id=user_id, status="RECYCLED"
            )
