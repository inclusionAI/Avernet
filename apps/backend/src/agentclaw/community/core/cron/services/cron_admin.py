"""Disable-only administration; owner identity is resolved from stored Bot data."""
from __future__ import annotations

import logging
import re

from injector import inject

from agentclaw.community.core.access.admin_scopes import cron_admin
from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from agentclaw.community.core.cron.cron_admin_service_protocol import CronAdminServiceProtocol
from agentclaw.community.core.cron.cron_relay_service_protocol import CronRelayServiceProtocol
from agentclaw.community.core.cron.errors import CronRelayError
from agentclaw.community.core.repository.protocols.bot import BotRepository

logger = logging.getLogger(__name__)


class CronAdminService(CronAdminServiceProtocol):
    @inject
    def __init__(self, bots: BotRepository, relay: CronRelayServiceProtocol):
        self._bots = bots
        self._relay = relay

    def _resolve_bot(self, operator_id: str, bot_id: str, runtime_stage: str) -> dict:
        if not operator_id or operator_id == "anonymous" or operator_id not in cron_admin():
            raise CronRelayError("cron_admin permission required", error_code=403)
        if runtime_stage not in {"verify", "online"}:
            raise CronRelayError("runtime_stage must be verify or online", error_code=400)
        if not bot_id or bot_id == "all":
            raise CronRelayError("A specific bot_id is required", error_code=400)
        try:
            bot = self._bots.get_unique_by_id(bot_id)
        except BotLookupAmbiguousError as exc:
            raise CronRelayError("bot_id matches multiple Bots", error_code=409) from exc
        if bot is None:
            raise CronRelayError("Bot not found", error_code=404)
        if bot.get("bot_type") != "service":
            raise CronRelayError("Only service Bots support cron administration", error_code=403)
        if not bot.get("owner_id") or bot["owner_id"] == "anonymous":
            raise CronRelayError("Bot owner identity unavailable", error_code=409)
        return bot

    @staticmethod
    def _result(result: dict) -> dict:
        # Preserve per-instance receipts; missing acknowledgement is not success.
        result = dict(result)
        success = result.get("success") is True and not result.get("failed_targets")
        result["success"] = success
        result["error_code"] = 200 if success else result.get("error_code", 502)
        if not success and result["error_code"] == 200:
            result["error_code"] = 502
        if not success and result.get("message", "OK") == "OK":
            result["message"] = "Cron operation failed or incomplete"
        return result

    async def list_tasks(
        self, *, operator_id: str, bot_id: str, runtime_stage: str,
    ) -> dict:
        bot = self._resolve_bot(operator_id, bot_id, runtime_stage)
        result = await self._relay.list_all_crons(
            user_id=bot["owner_id"], nick_name=bot.get("owner_name") or bot["owner_id"],
            bot_id=bot_id, runtime_stage=runtime_stage,
        )
        return self._result(result)

    async def disable_task(
        self, *, operator_id: str, bot_id: str, task_id: str,
        runtime_stage: str, reason: str,
    ) -> dict:
        owner_id = ""
        outcome = "error"
        error_code = 500
        failed_targets: list[dict] = []
        try:
            bot = self._resolve_bot(operator_id, bot_id, runtime_stage)
            owner_id = bot["owner_id"]
            # Relay interpolates the ID in an adapter URL: reject path/query delimiters.
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", task_id):
                raise CronRelayError("Invalid task_id", error_code=400)
            if not reason.strip() or len(reason) > 1000:
                raise CronRelayError("A reason of 1 to 1000 characters is required", error_code=400)
            result = self._result(await self._relay.update_cron(
                bot_id=bot_id, user_id=owner_id,
                nick_name=bot.get("owner_name") or owner_id, task_id=task_id,
                body={"enabled": False}, runtime_stage=runtime_stage,
            ))
            outcome = "success" if result["success"] else "failed_or_partial"
            error_code = result["error_code"]
            failed_targets = result.get("failed_targets", [])
            return result
        except CronRelayError as exc:
            error_code = exc.error_code
            raise
        finally:
            # %r escapes user-supplied newlines, preserving one audit record per call.
            logger.warning(
                "CRON_ADMIN_DISABLE operator_id=%r owner_id=%r bot_id=%r task_id=%r "
                "runtime_stage=%r reason=%r outcome=%s error_code=%s failed_targets=%r",
                operator_id, owner_id, bot_id, task_id, runtime_stage, reason,
                outcome, error_code, failed_targets,
            )
