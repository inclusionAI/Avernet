"""ActivateBotService — reactivate a RECYCLED bot.

Flow:
  1. Fetch bot, check state.
  2. REACTIVATING → friendly early return (idempotent).
  3. non-RECYCLED  → InvalidBotStateError.
  4. RECYCLED      → update_status(REACTIVATING) + spawn background thread
                      that calls passport unfreeze, verifies the runtime token,
                      then starts the bot;
                      on failure rolls back to RECYCLED, and re-freezes the
                      passport only when the unfreeze itself is not the step
                      that must survive for the next attempt (see
                      ``_reactivate_async``).
"""
from __future__ import annotations

import threading
import time

from injector import inject

from agentclaw.community.core.bot_dormant.protocols import BotServiceProtocol
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.utils.avernet_tenant import bind_current_avernet_tenant


logger = get_logger()

# ``unfreeze_agent_passport`` brings the credential online, but the runtime
# token only becomes queryable once that change has propagated through the
# passport provider. The plugin protocol asks implementations to return after
# that postcondition holds; providers that resolve it asynchronously make the
# first ``query_token`` race the propagation. Poll instead of trusting a single
# read: the caller already returned REACTIVATING, so this wait is off the
# request path.
TOKEN_VERIFY_ATTEMPTS = 5
TOKEN_VERIFY_BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0)


class InvalidBotStateError(Exception):
    """Raised when activate is called on a bot that is not RECYCLED."""


class BotNotFoundError(Exception):
    """Raised when the requested bot does not exist for the owner."""


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
        Raises ``BotNotFoundError`` if the bot does not exist and
        ``InvalidBotStateError`` if it is not RECYCLED (or REACTIVATING).
        """
        bot = self._bot_service.get_bot(bot_id=bot_id, user_id=user_id)
        if not bot:
            logger.warning(
                "[activate] bot not found bot_id=%s user_id=%s",
                bot_id, user_id,
            )
            raise BotNotFoundError(f"bot not found: {bot_id}")

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
            target=bind_current_avernet_tenant(self._reactivate_async),
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

        token = self._verify_token(bot_id=bot_id, user_id=user_id)
        if not token:
            # The credential is online — only the token read failed. Re-freezing
            # here would undo the one step that succeeded and hand the next
            # attempt the same cold start, turning a propagation delay into a
            # bot that can never be activated no matter how often the user
            # retries. Leave it online so a retry resumes from settled state;
            # this is the same state the ops ``unfreeze-passport-one`` endpoint
            # produces deliberately. Cost: a RECYCLED bot is never re-scanned
            # (filter_candidates takes ACTIVE only), so nothing re-freezes this
            # credential automatically — grep the marker below to reconcile.
            logger.error(
                "[activate] passport token unavailable after %d attempts, "
                "leaving credential online for retry "
                "event=token_verify_exhausted bot_id=%s user_id=%s",
                TOKEN_VERIFY_ATTEMPTS,
                bot_id,
                user_id,
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

    def _verify_token(self, *, bot_id: str, user_id: str) -> str | None:
        """Poll ``query_token`` until the unfrozen credential yields a token.

        Returns the token, or None once every attempt is exhausted. A raising
        provider is treated the same as an empty answer — both mean "not usable
        yet", and only the final outcome decides whether the bot starts.
        """
        logger.info(
            "[activate] passport token verify start bot_id=%s user_id=%s attempts=%d",
            bot_id,
            user_id,
            TOKEN_VERIFY_ATTEMPTS,
        )
        for attempt in range(1, TOKEN_VERIFY_ATTEMPTS + 1):
            try:
                token = self._passport.query_token(
                    bot_id=bot_id,
                    owner_workno=user_id,
                )
            except Exception as e:
                token = None
                logger.warning(
                    "[activate] passport token query raised "
                    "bot_id=%s user_id=%s attempt=%d/%d: %s",
                    bot_id, user_id, attempt, TOKEN_VERIFY_ATTEMPTS, e,
                )
            if token:
                logger.info(
                    "[activate] passport token verify success "
                    "bot_id=%s user_id=%s attempt=%d",
                    bot_id, user_id, attempt,
                )
                return token
            if attempt < TOKEN_VERIFY_ATTEMPTS:
                delay = TOKEN_VERIFY_BACKOFF_SECONDS[
                    min(attempt - 1, len(TOKEN_VERIFY_BACKOFF_SECONDS) - 1)
                ]
                logger.info(
                    "[activate] passport token not ready, retrying "
                    "bot_id=%s user_id=%s attempt=%d/%d delay=%.1fs",
                    bot_id, user_id, attempt, TOKEN_VERIFY_ATTEMPTS, delay,
                )
                time.sleep(delay)
        return None
