"""Build and publish a complete MCP/CLI Passport scope for MCP sync."""
from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Callable, Optional

from agentclaw.community.core.mcp.services._defaults import get_default_cli_items
from agentclaw.community.core.mcp.services.passport_scope import (
    passport_mcp_items_from_entries,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.identity import (
    CallerIdentityRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.passport import PassportPlugin, extract_cli_items


logger = get_logger()


def update_mcp_passport_scope(
    *,
    passport_plugin: PassportPlugin,
    bot_repository: BotRepository,
    caller_identity_repository: CallerIdentityRepositoryProtocol,
    bot_id: str,
    user_id: str,
    owner_id: str,
    synced_mcps: list[dict[str, Any]],
    engine_type: Optional[str],
    scope_builder: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Update complete MCP scope; abort if identity or metadata cannot be read."""
    scope_started = time.monotonic()
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
    except Exception as exc:
        error = "获取 bot 信息失败，无法安全解析默认 CLI 范围"
        logger.error("[MCPSyncService] %s, bot_id=%s", error, bot_id)
        logger.error(
            "agentpass_mcp_scope_update_failed bot_id=%s engine_type=%s "
            "branch=mcp_sync stage=bot_metadata status=failed error_type=%s duration_ms=%s",
            bot_id,
            engine_type,
            type(exc).__name__,
            int((time.monotonic() - scope_started) * 1000),
        )
        return {"success": False, "error": error}

    identity_modes: Mapping[str, object] = {}
    try:
        if bot:
            identity_modes = caller_identity_repository.list_draft_call_types(
                int(bot["id"]), str(engine_type)
            )
        else:
            logger.info(
                "[MCPSyncService] 未找到持久化 bot，按 owner 刷新 MCP scope: bot_id=%s",
                bot_id,
            )
        mcp_items = passport_mcp_items_from_entries(
            synced_mcps, identity_modes=identity_modes
        )
    except Exception as exc:
        error = "查询 MCP 调用身份失败"
        logger.error("[MCPSyncService] %s, bot_id=%s", error, bot_id)
        logger.error(
            "agentpass_mcp_scope_update_failed bot_id=%s engine_type=%s "
            "branch=mcp_sync stage=identity status=failed error_type=%s duration_ms=%s",
            bot_id,
            engine_type,
            type(exc).__name__,
            int((time.monotonic() - scope_started) * 1000),
        )
        return {"success": False, "error": error}

    synced_server_codes = [item["mcp_code"] for item in mcp_items]
    caller_mcp_codes = [
        item["mcp_code"] for item in mcp_items if item["identity_mode"] == "caller"
    ]

    # Every overwrite reads the complete MCP+CLI snapshot first. The desired
    # membership remains this writer's own decision; history only restores an
    # identity when no sparse row exists.
    snapshot_started = time.monotonic()
    logger.info(
        "agentpass_mcp_scope_snapshot_requested bot_id=%s engine_type=%s "
        "branch=mcp_sync mcp_count=%s cli_count=%s duration_ms=%s",
        bot_id,
        engine_type,
        "unknown",
        "unknown",
        0,
    )
    try:
        passport = passport_plugin.query_agent_passport(bot_id, user_id)
    except Exception as exc:
        error = "查询 CLI 范围失败"
        logger.error("[MCPSyncService] %s", error)
        logger.error(
            "agentpass_mcp_scope_snapshot_failed bot_id=%s engine_type=%s "
            "branch=mcp_sync stage=snapshot status=failed error_type=%s duration_ms=%s",
            bot_id,
            engine_type,
            type(exc).__name__,
            int((time.monotonic() - snapshot_started) * 1000),
        )
        return {"success": False, "error": error}

    snapshot_mcp_count = (
        len(passport.get("mcps", []))
        if isinstance(passport, Mapping) and isinstance(passport.get("mcps"), list)
        else "unknown"
    )
    snapshot_cli_count = (
        len(passport.get("clis", []))
        if isinstance(passport, Mapping) and isinstance(passport.get("clis"), list)
        else "unknown"
    )
    logger.info(
        "agentpass_mcp_scope_snapshot_succeeded bot_id=%s engine_type=%s "
        "branch=mcp_sync stage=snapshot status=succeeded mcp_count=%s cli_count=%s duration_ms=%s",
        bot_id,
        engine_type,
        snapshot_mcp_count,
        snapshot_cli_count,
        int((time.monotonic() - snapshot_started) * 1000),
    )

    current_cli_items = extract_cli_items(passport)
    default_cli_items = get_default_cli_items(
        engine_type,
        template_type,
        ext_info={"template_config": template_config} if template_config else None,
    )
    try:
        resource_scope = scope_builder(
            passport,
            desired_mcp_items=mcp_items,
            mcp_identity_modes=identity_modes,
            additional_cli_items=default_cli_items,
        )
    except Exception as exc:
        error = "构建 Passport 完整范围失败"
        logger.error(
            "agentpass_mcp_scope_snapshot_failed bot_id=%s engine_type=%s "
            "branch=mcp_sync stage=build status=failed error_type=%s duration_ms=%s",
            bot_id,
            engine_type,
            type(exc).__name__,
            int((time.monotonic() - snapshot_started) * 1000),
        )
        return {"success": False, "error": error}
    mcp_items = resource_scope["mcp_items"]
    synced_server_codes = resource_scope["mcp_codes"]
    cli_items = resource_scope["cli_items"]
    caller_mcp_codes = [
        item["mcp_code"] for item in mcp_items if item["identity_mode"] == "caller"
    ]
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

    update_started = time.monotonic()
    try:
        # resource_scope 是完整快照：MCP 身份与 CLI 都必须回传，避免覆盖丢失授权。
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
        logger.info(
            "agentpass_mcp_scope_update_requested bot_id=%s engine_type=%s "
            "branch=mcp_sync stage=update mcp_count=%s cli_count=%s duration_ms=%s",
            bot_id,
            engine_type,
            len(resource_scope["mcp_items"]),
            len(resource_scope["cli_items"]),
            0,
        )
        passport_plugin.update_passport(
            bot_id=bot_id,
            user_id=user_id,
            resource_scope=resource_scope,
            bot_name=bot_name,
            bot_desc=bot_desc,
            engine_type=engine_type,
        )
        logger.info(
            "agentpass_mcp_scope_update_succeeded bot_id=%s engine_type=%s "
            "branch=mcp_sync stage=update status=succeeded mcp_count=%s cli_count=%s duration_ms=%s",
            bot_id,
            engine_type,
            len(resource_scope["mcp_items"]),
            len(resource_scope["cli_items"]),
            int((time.monotonic() - update_started) * 1000),
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
    except Exception as exc:
        error = "更新 passport 失败"
        logger.error("[MCPSyncService] %s", error)
        logger.error(
            "agentpass_mcp_scope_update_failed bot_id=%s engine_type=%s "
            "branch=mcp_sync stage=update status=failed error_type=%s duration_ms=%s",
            bot_id,
            engine_type,
            type(exc).__name__,
            int((time.monotonic() - update_started) * 1000),
        )
        return {"success": False, "error": error}


__all__ = ["update_mcp_passport_scope"]
