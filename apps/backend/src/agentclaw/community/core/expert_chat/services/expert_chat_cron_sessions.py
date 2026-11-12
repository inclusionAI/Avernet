"""Discover runtime-created cron sessions inside an independent Caller instance."""
from __future__ import annotations

import re
from time import monotonic
from typing import Any

from agentclaw.community.log import get_logger

logger = get_logger()
_CRON_SESSION_KEY = re.compile(r"agent:[^:]+:cron:[^:]+(?::run:[^:]+)?")
_PAGE_SIZE = 500


class ExpertChatCronSessionsMixin:
    def _prepare_existing_caller_connection(
        self, bot: dict[str, Any], user_id: str, owner_id: str, *,
        bcn_friend_authorized: bool, actor_user_id: str | None,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Resolve an existing Caller for reads without entering lifecycle flows."""
        bot.pop("_caller_instance_binding_id", None)
        connection_user_id = self._session_read_actor(
            bot, user_id, actor_user_id, bcn_friend_authorized,
        )
        self._online_chat_binding(bot)
        instance = self._instance_service.get_existing_caller_instance(
            user_id, bot["bot_id"], owner_id,
        )
        if instance is None:
            logger.info("caller_cron_sessions_skipped bot_id=%s user_id=%s reason=instance_missing", bot["bot_id"], user_id)
            return None, False
        ext = instance.get("ext") or {}
        binding_id = ext.get("binding_id")
        if instance.get("status") != "success" or not binding_id:
            logger.info("caller_cron_sessions_skipped bot_id=%s user_id=%s reason=instance_not_ready", bot["bot_id"], user_id)
            return None, True
        # COSEC: Instance lookup uses the authenticated session subject and exact Bot.
        bot["binding_id"] = binding_id
        bot["_caller_instance_binding_id"] = binding_id
        return self._get_connection(
            bot, connection_user_id, bcn_friend_authorized=bcn_friend_authorized,
        ), False

    async def _discover_caller_cron_sessions(
        self, bot: dict[str, Any], connection: dict[str, Any], user_id: str,
        owner_id: str, rows: list[dict[str, Any]], session_key: str | None,
    ) -> dict[str, dict[str, Any]]:
        # COSEC: Only a successfully resolved subject-owned Caller binding can
        # authorize unindexed cron sessions; shared runtimes retain index-only access.
        binding_id = bot.get("_caller_instance_binding_id")
        ctx, strategy = self._resolve_chat_engine_strategy(bot, connection)
        if (bot.get("bot_type") != "service" or bot.get("call_type") != "caller"
                or not binding_id or binding_id != bot.get("binding_id")
                or not strategy.supports_caller_cron_session_discovery(ctx)):
            return {}
        matched = {}
        seen: set[str] = set()
        owned_keys = {row["session_key"] for row in rows}
        offset = 0
        while True:
            started = monotonic()
            logger.info(
                "caller_cron_sessions_request system=engine operation=list_sessions "
                "method=GET route=/api/sessions bot_id=%s owner_id=%s user_id=%s "
                "binding_id=%s offset=%s limit=%s",
                bot["bot_id"], owner_id, user_id, binding_id, offset, _PAGE_SIZE,
            )
            try:
                response = await self._transport.invoke(
                    connection, "GET", "/api/sessions",
                    params={"limit": _PAGE_SIZE, "offset": offset},
                )
                data = response.get("data")
                if response.get("success") is False or not isinstance(data, list):
                    raise ValueError("Invalid session list response")
            except Exception as error:
                logger.warning(
                    "caller_cron_sessions_failed system=engine operation=list_sessions "
                    "bot_id=%s user_id=%s binding_id=%s offset=%s status=failed "
                    "error_type=%s duration_ms=%.2f",
                    bot["bot_id"], user_id, binding_id, offset,
                    type(error).__name__, (monotonic() - started) * 1000,
                )
                break
            page_ids = {item["id"] for item in data if isinstance(item, dict)
                        and isinstance(item.get("id"), str) and item["id"]}
            selected = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                key = item.get("id")
                if (not isinstance(key, str) or not _CRON_SESSION_KEY.fullmatch(key)
                        or key in seen or (session_key is not None and key != session_key)):
                    continue
                if key not in owned_keys:
                    self._repo.add_owned_session(user_id, bot["bot_id"], owner_id, key)
                    rows.append({"user_id": user_id, "bot_id": bot["bot_id"],
                                 "owner_id": owner_id, "session_key": key,
                                 "gmt_create": item.get("gmt_created") or "",
                                 "gmt_modified": item.get("gmt_modified") or ""})
                    owned_keys.add(key)
                matched[key] = item
                selected.append(key)
            logger.info(
                "caller_cron_sessions_success system=engine operation=list_sessions "
                "bot_id=%s user_id=%s binding_id=%s offset=%s status=success "
                "returned_count=%s selected_keys=%s duration_ms=%.2f",
                bot["bot_id"], user_id, binding_id, offset, len(data), selected,
                (monotonic() - started) * 1000,
            )
            if not page_ids - seen:
                break
            seen.update(page_ids)
            # Runtime filters can shorten a page after applying its offset/limit.
            offset += _PAGE_SIZE
        return matched
