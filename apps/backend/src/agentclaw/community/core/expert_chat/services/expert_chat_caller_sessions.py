"""Discover runtime-created sessions inside an independent Caller instance."""
from __future__ import annotations

from time import monotonic
from typing import Any
from urllib.parse import quote

from agentclaw.community.core.expert_chat.errors import BotNotFoundError, ConnectionError

from agentclaw.community.log import get_logger

logger = get_logger()
_PAGE_SIZE = 500


class ExpertChatCallerSessionsMixin:
    def _is_independent_caller(self, bot: dict[str, Any]) -> bool:
        ctx, strategy = self._resolve_chat_engine_strategy(bot, {})
        return (bot.get("bot_type") == "service" and bot.get("call_type") == "caller"
                and strategy.requires_caller_chat_instance(ctx))

    async def _invoke_caller_session(self, bot, connection, user_id, method, path, **kwargs):
        """Keep runtime failures and credentials behind the domain boundary."""
        started = monotonic()
        identity = (bot["bot_id"], bot["owner_id"], user_id, bot["binding_id"], method, path)
        # COSEC: Log only known scalar business fields; credentials and arbitrary
        # nested payloads, connection data and message content never enter logs.
        params = kwargs.get("params") or {}
        fields = {key: params[key] for key in ("title", "favorited", "limit", "offset")
                  if key in params and isinstance(params[key], (str, int, bool))}
        logger.info("caller_runtime_session_request system=engine bot_id=%s owner_id=%s user_id=%s binding_id=%s operation=%s route=%s fields=%s", *identity, fields)
        try:
            response = await self._transport.invoke(connection, method, path, **kwargs)
            if response.get("success") is False:
                raise ValueError("Runtime operation failed")
        except Exception as error:
            logger.warning("caller_runtime_session_failed system=engine bot_id=%s owner_id=%s user_id=%s binding_id=%s operation=%s route=%s status=failed error_type=%s duration_ms=%.2f",
                           *identity, type(error).__name__, (monotonic() - started) * 1000)
            if self._is_adapter_not_found(error):
                raise BotNotFoundError("Session不存在或不属于当前用户") from None
            raise ConnectionError("无法连接到Bot服务", error_code="5001") from None
        data = response.get("data")
        returned_count = len(data) if isinstance(data, list) else int(isinstance(data, dict))
        logger.info("caller_runtime_session_success system=engine bot_id=%s owner_id=%s user_id=%s binding_id=%s operation=%s route=%s status=success returned_count=%s duration_ms=%.2f",
                    *identity, returned_count, (monotonic() - started) * 1000)
        return response

    async def _require_caller_runtime_session(self, bot, connection, user_id, session_key):
        # COSEC: A supplied key never selects a container; the authenticated
        # subject's successful instance binding fixes the runtime before lookup.
        if not session_key.strip() or connection is None:
            raise BotNotFoundError("Session不存在或不属于当前用户")
        response = await self._invoke_caller_session(bot, connection, user_id, "GET", f"/api/sessions/{quote(session_key, safe='')}")
        item = response.get("data")
        if not isinstance(item, dict) or item.get("id") != session_key:
            logger.warning("caller_runtime_session_invalid bot_id=%s user_id=%s status=not_found", bot["bot_id"], user_id)
            raise BotNotFoundError("Session不存在或不属于当前用户")
        return item

    async def _prepare_authorized_session_connection(self, bot, user_id, owner_id, session_key, iam_token, *, bcn_friend_authorized=False, actor_user_id=None):
        if self._is_independent_caller(bot):
            connection, need_poll = await self._prepare_existing_caller_connection(
                bot, user_id, owner_id, bcn_friend_authorized=bcn_friend_authorized, actor_user_id=actor_user_id, iam_token=iam_token)
            if need_poll:
                instance = self._instance_service.get_existing_caller_instance(user_id, bot["bot_id"], owner_id)
                if instance and instance.get("status") == "init":
                    return None, True
                raise ConnectionError("Bot服务正在启动，请稍后重试", error_code="5001")
            await self._require_caller_runtime_session(bot, connection, user_id, session_key)
            return connection, False
        if not self._repo.get_owned_session(user_id, bot["bot_id"], owner_id, session_key):
            raise BotNotFoundError("Session不存在或不属于当前用户")
        return await self._prepare_chat_connection(bot, user_id, owner_id, iam_token,
            bcn_friend_authorized=bcn_friend_authorized, **({"actor_user_id": actor_user_id} if actor_user_id is not None else {}))

    async def _list_caller_sessions(self, bot, user_id, owner_id, session_key, favorite_only, limit, offset, *, bcn_friend_authorized, actor_user_id, iam_token=None):
        connection, need_poll = await self._prepare_existing_caller_connection(bot, user_id, owner_id,
            bcn_friend_authorized=bcn_friend_authorized, actor_user_id=actor_user_id, iam_token=iam_token)
        if connection is None or need_poll:
            return {"total": 0, "items": [], **({"need_poll": True} if need_poll else {})}
        if session_key is not None:
            try:
                item = await self._require_caller_runtime_session(bot, connection, user_id, session_key)
            except BotNotFoundError:
                return {"total": 0, "items": []}
            matched = {session_key: item}
        else:
            matched = await self._discover_caller_runtime_sessions(bot, connection, user_id)
        if favorite_only:
            try:
                response = await self._invoke_caller_session(bot, connection, user_id, "GET", "/api/session-favorites",
                    params={"user_id": user_id, "limit": 10_000, "offset": 0})
                favorites = response.get("data") or []
            except (ConnectionError, BotNotFoundError):
                favorites = []
            favorite_keys = {item.get("id") for item in favorites}
            matched = {key: item for key, item in matched.items() if key in favorite_keys}
        items = []
        for key, item in matched.items():
            enriched = self._placeholder_session({"session_key": key, "user_id": user_id, "bot_id": bot["bot_id"]})
            enriched.update(item)
            last_message = item.get("last_message")
            if isinstance(last_message, dict) and last_message.get("gmt_created"):
                enriched["gmt_modified"] = last_message["gmt_created"]
            items.append(enriched)
        items.sort(key=lambda item: (self._session_sort_key(item), item["id"]), reverse=True)
        return {"total": len(items), "items": items[offset:offset + limit]}

    async def _prepare_existing_caller_connection(
        self, bot: dict[str, Any], user_id: str, owner_id: str, *,
        bcn_friend_authorized: bool, actor_user_id: str | None, iam_token: str | None = None,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Resolve an existing Caller for reads without entering lifecycle flows."""
        connection_user_id = self._session_read_actor(
            bot, user_id, actor_user_id, bcn_friend_authorized,
        )
        ctx, strategy = self._resolve_chat_engine_strategy(bot, {})
        # COSEC: Shared runtimes cannot authorize subject ownership via Caller rows.
        if not strategy.requires_caller_chat_instance(ctx):
            logger.info("caller_runtime_sessions_skipped bot_id=%s user_id=%s reason=shared_runtime", bot["bot_id"], user_id)
            return None, False
        self._online_chat_binding(bot)
        instance = self._instance_service.get_existing_caller_instance(
            user_id, bot["bot_id"], owner_id,
        )
        if instance and instance.get("status") == "init" and all(
            (instance.get("ext") or {}).get(key) for key in ("bot_uuid", "binding_id", "baas_publish_id")
        ):
            instance = await self._instance_service.refresh_existing_caller_instance(
                user_id, bot["bot_id"], owner_id,
                # COSEC: an owner inspecting a friend's session cannot install their own IAM as that Caller.
                iam_token=iam_token if actor_user_id in (None, user_id) else None,
            )
        if instance is None:
            logger.info("caller_runtime_sessions_skipped bot_id=%s user_id=%s reason=instance_missing", bot["bot_id"], user_id)
            return None, False
        ext = instance.get("ext") or {}
        binding_id = ext.get("binding_id")
        if instance.get("status") != "success" or not binding_id:
            logger.info("caller_runtime_sessions_skipped bot_id=%s user_id=%s reason=instance_not_ready", bot["bot_id"], user_id)
            return None, True
        # COSEC: Instance lookup uses the authenticated session subject and exact Bot.
        bot["binding_id"] = binding_id
        connection = self._get_connection(
            bot, connection_user_id, bcn_friend_authorized=bcn_friend_authorized,
        )
        connection["engine_type"] = ctx.active_engine
        return connection, False

    async def _discover_caller_runtime_sessions(
        self, bot: dict[str, Any], connection: dict[str, Any], user_id: str,
    ) -> dict[str, dict[str, Any]]:
        """Read all pages from the already-authorized Caller container."""
        binding_id = bot["binding_id"]
        matched = {}
        offset = 0
        while True:
            started = monotonic()
            logger.info(
                "caller_runtime_sessions_request system=engine operation=list_sessions "
                "method=GET route=/api/sessions bot_id=%s owner_id=%s user_id=%s "
                "binding_id=%s offset=%s limit=%s",
                bot["bot_id"], bot["owner_id"], user_id, binding_id, offset, _PAGE_SIZE,
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
                    "caller_runtime_sessions_failed system=engine operation=list_sessions "
                    "bot_id=%s user_id=%s binding_id=%s offset=%s status=failed "
                    "error_type=%s duration_ms=%.2f",
                    bot["bot_id"], user_id, binding_id, offset,
                    type(error).__name__, (monotonic() - started) * 1000,
                )
                break
            page_ids = {item["id"] for item in data if isinstance(item, dict)
                        and isinstance(item.get("id"), str) and item["id"].strip()}
            new_page_ids = page_ids - matched.keys()
            selected = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                key = item.get("id")
                if (not isinstance(key, str) or not key.strip()
                        or key in matched):
                    continue
                matched[key] = item
                selected.append(key)
            logger.info(
                "caller_runtime_sessions_success system=engine operation=list_sessions "
                "bot_id=%s user_id=%s binding_id=%s offset=%s status=success "
                "returned_count=%s selected_keys=%s duration_ms=%.2f",
                bot["bot_id"], user_id, binding_id, offset, len(data), selected,
                (monotonic() - started) * 1000,
            )
            if not new_page_ids:
                break
            # Runtime filters can shorten a page after applying its offset/limit.
            offset += _PAGE_SIZE
        return matched
