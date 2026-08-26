"""Writing a bot's MCP identity and scope into Passport.

The other half of an MCP sync. ``MCPSyncService`` pushes MCP configuration to a
bot's **device**; these two write what the bot is *allowed* to reach into
**Passport**, for the frontend's permission checks and for TCAuth. They share
that trigger and nothing else: no device is resolved, dispatched to, or touched
here, and neither function is on the request's device round-trip path.

They take their collaborators as arguments rather than holding them, so a caller
that swaps ``passport_update`` (or either repository) after construction still
has that swap honoured — which is how ``MCPSyncService``'s own callers and tests
address these dependencies.

The pure entry-shaping helpers these build on live next door in
:mod:`passport_scope`.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, TYPE_CHECKING

from agentclaw.community.core.mcp.services._defaults import get_default_cli_items
from agentclaw.community.core.mcp.services.passport_scope import (
    merge_passport_cli_items,
    passport_mcp_items_from_entries,
)
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.repository.protocols.bot import BotRepository
    from agentclaw.community.core.repository.protocols.identity import (
        CallerIdentityRepositoryProtocol,
    )
    from agentclaw.community.plugin_api.passport import PassportPlugin

logger = get_logger()


async def update_agent_principal_identity(
    *,
    passport_update: "PassportPlugin",
    user_id: str,
    entity_id: str,
    bot_id: str,
    entity_type: str,
    engine_type: str,
    active_mcps: list[dict[str, Any]],
    identity_modes: Mapping[str, object],
) -> dict[str, Any]:
    """Replace Agent Principal MCP identity metadata without device sync."""
    del entity_id, entity_type, engine_type
    try:
        mcp_items = passport_mcp_items_from_entries(
            active_mcps,
            identity_modes=identity_modes,
        )
        # 当前 Passport MCP 参数不含 token；保留完整条目以定位 TCAuth 前置校验失败值。
        logger.info(
            "[MCPSyncService] Passport update request: "
            "operation=caller_mcp_identity_sync, bot_id=%s, user_id=%s, "
            "mcp_items=%s",
            bot_id,
            user_id,
            mcp_items,
        )
        passport_update.update_mcp_identity_to_agent_principal(
            bot_id=bot_id,
            user_id=user_id,
            mcp_items=mcp_items,
        )
    except Exception as exc:
        logger.warning(
            "caller_agent_principal_sync_failed bot_id=%s error_type=%s",
            bot_id,
            type(exc).__name__,
        )
        return {"success": False, "error": "Agent Principal update failed"}

    logger.info(
        "caller_agent_principal_sync_succeeded bot_id=%s mcp_count=%s",
        bot_id,
        len(mcp_items),
    )
    return {"success": True}


async def update_mcp_scope(
    *,
    passport_update: "PassportPlugin",
    bot_repository: "BotRepository",
    caller_identity_repository: "CallerIdentityRepositoryProtocol",
    bot_id: str,
    user_id: str,
    owner_id: str,
    synced_mcps: list[dict[str, Any]],
    engine_type: Optional[str] = None,
) -> dict[str, Any]:
    """更新完整 MCP scope；身份或 bot 元数据不可读取时中止覆盖。"""
    bot_name: Optional[str] = None
    bot_desc: Optional[str] = None
    template_type: Optional[str] = None
    template_config: Optional[Mapping[str, Any]] = None
    try:
        bot = bot_repository.get_by_id_and_owner(bot_id, owner_id)
        if bot:
            bot_name = bot.get("bot_name")
            bot_desc = bot.get("bot_desc")
            template_type = bot.get("template_type")
            raw_template_config = bot.get("template_config")
            template_config = raw_template_config if isinstance(raw_template_config, Mapping) else None
            engine_type = bot.get("active_engine") or bot.get("engine_type") or engine_type
    except Exception as e:
        error = f"获取 bot 信息失败，无法安全解析默认 CLI 范围: {e}"
        logger.error("[MCPSyncService] %s, bot_id=%s", error, bot_id)
        return {"success": False, "error": error}

    identity_modes: Mapping[str, object] = {}
    try:
        if bot:
            identity_modes = caller_identity_repository.list_draft_call_types(int(bot["id"]), str(engine_type))
        else:
            logger.info("[MCPSyncService] 未找到持久化 bot，按 owner 刷新 MCP scope: bot_id=%s", bot_id)
        mcp_items = passport_mcp_items_from_entries(synced_mcps, identity_modes=identity_modes)
    except Exception as e:
        error = f"查询 MCP 调用身份失败: {e}"
        logger.error("[MCPSyncService] %s, bot_id=%s", error, bot_id)
        return {"success": False, "error": error}

    synced_server_codes = [item["mcp_code"] for item in mcp_items]
    caller_mcp_codes = [
        item["mcp_code"] for item in mcp_items if item["identity_mode"] == "caller"
    ]

    # MCP 同步触发 resourceManifest 更新时，要回填当前 CLI，避免覆盖式更新丢失 CLI 授权。
    try:
        current_cli_items = passport_update.query_passport_clis(bot_id, user_id)
    except Exception as e:
        error = f"查询 CLI 范围失败: {e}"
        logger.error("[MCPSyncService] %s", error)
        return {"success": False, "error": error}

    default_cli_items = get_default_cli_items(
        engine_type,
        template_type,
        ext_info={"template_config": template_config}
        if template_config
        else None,
    )
    cli_items = merge_passport_cli_items(current_cli_items, default_cli_items)
    if default_cli_items:
        logger.info(
            "[MCPSyncService] 合并默认 CLI 范围: bot_id=%s, current_clis=%s, "
            "default_clis=%s, merged_clis=%s, engine_type=%s, template_type=%s",
            bot_id,
            current_cli_items,
            default_cli_items,
            cli_items,
            engine_type,
            template_type,
        )

    try:
        # resource_scope 是完整快照：MCP 身份与 CLI 都必须回传，避免覆盖丢失授权。
        resource_scope = {
            "mcp_codes": synced_server_codes,
            "mcp_items": mcp_items,
            "cli_items": cli_items,
        }
        # 当前 Passport MCP 参数不含 token；保留完整请求以定位 TCAuth 前置校验失败值。
        logger.info(
            "[MCPSyncService] Passport update request: "
            "operation=mcp_scope_refresh, bot_id=%s, user_id=%s, "
            "resource_scope=%s, bot_name=%s, bot_desc=%s, engine_type=%s",
            bot_id,
            user_id,
            resource_scope,
            bot_name,
            bot_desc,
            engine_type,
        )
        passport_update.update_passport(
            bot_id=bot_id,
            user_id=user_id,
            resource_scope=resource_scope,
            bot_name=bot_name,
            bot_desc=bot_desc,
            engine_type=engine_type,
        )
        logger.info(
            "[MCPSyncService] updatePassport 成功: "
            "bot_id=%s, user_id=%s, mcps=%s, caller_mcps=%s, clis=%s, "
            "engine_type=%s, bot_name=%s",
            bot_id,
            user_id,
            synced_server_codes,
            caller_mcp_codes,
            cli_items,
            engine_type,
            bot_name,
        )
        return {"success": True}
    except Exception as e:
        error = f"更新 passport 失败: {e}"
        logger.error("[MCPSyncService] %s", error)
        return {"success": False, "error": error}


__all__ = ["update_agent_principal_identity", "update_mcp_scope"]
