"""Build and deliver one MCP payload from separate Bot and user scopes."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from agentclaw.community.core.bot_management.addressing import BotAddressResolver, ResolvedBotContext
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.core.devices.services.device_context import DeviceContext, DeviceNotBoundError
from agentclaw.community.core.devices.services.device_context_resolver import DeviceContextResolver
from agentclaw.community.core.mcp.mcp_config_service_protocol import MCPConfigServiceProtocol
from agentclaw.community.core.mcp.url_resolution import effective_mcp_url_override
from agentclaw.community.log import get_logger

logger = get_logger()


def resolve_detail_bot(
    bots: BotRepository, *, bot_id: str, entity_id: str | None, legacy_owner_id: str,
) -> ResolvedBotContext:
    """Explicit entities are exact; only omitted entities use a legacy locator."""
    resolver = BotAddressResolver(bots)
    if entity_id is not None:
        return resolver.resolve(bot_id=bot_id, entity_id=entity_id)
    return resolver.resolve(bot_id=bot_id, legacy_owner_id=legacy_owner_id)


def resolve_detail_device(resolver: DeviceContextResolver, resource: ResolvedBotContext, operator_id: str) -> DeviceContext:
    """Use the already resolved Bot binding while retaining the delivery actor."""
    binding_id = resource.bot.get("binding_id")
    if binding_id is None:
        raise DeviceNotBoundError(f"DeviceContextResolver: no active binding for bot={resource.bot_id}")
    return resolver.resolve_for_binding(int(binding_id), operator_id, bot_id=resource.bot_id)


async def deliver_mcp_detail(
    *,
    config_service: MCPConfigServiceProtocol,
    plugin: DeviceSync,
    user_id: str,
    resource_entity_id: str,
    bot_id: str,
    mcp_data: dict[str, Any],
    api_key: str | None = None,
    custom_headers: Optional[dict[str, str]] = None,
    endpoint_env: Optional[str] = None,
    transport_protocol: Optional[str] = None,
    engine_type: Optional[str] = None,
) -> bool:
    """向单个 bot 的设备推送 MCP 配置。

    由 ``sync_mcp_detail``、``sync_mcp_detail_to_all_bots`` 与 ``_sync_mcp_details``
    复用：合并 payload 后经 per-bot 的 Core ``DeviceSync`` 服务投递。

    Args:
        plugin: 由 ``_device_sync_dispatcher_provider().dispatch(ctx)`` 取得的 per-bot ``DeviceSync`` 服务。
        user_id: 读取用户继承配置的人员 ID。
        resource_entity_id: 已解析的 Bot 稳定资源实体，不能用人员 ID 替代。
        mcp_data: MCP 配置数据。
        api_key: 可选的 API key，覆盖默认值。
        custom_headers: 可选的自定义请求头。
        endpoint_env: 可选的 endpoint 环境。
        transport_protocol: 可选的传输协议。
        engine_type: 引擎类型。

    Returns:
        同步是否成功。
    """
    # MCP Center 返回的字段是 camelCase 的 serverCode，
    # 而内部流转时可能已转成 snake_case 的 server_code，需要兼容两种写法。
    server_code = mcp_data.get("serverCode") or mcp_data.get("server_code", "")
    if not server_code:
        logger.warning("[MCPSyncService] MCP 数据缺少 server_code")
        return False

    bot_override = await asyncio.to_thread(
        config_service.get_bot_override,
        bot_id=bot_id,
        owner_id=resource_entity_id,
        server_code=server_code,
    )
    if bot_override:
        verdict = await asyncio.to_thread(
            config_service.validate_bot_override,
            user_id=user_id,
            server_code=server_code,
            config=bot_override,
            engine_type=engine_type,
        )
        if not verdict.get("valid"):
            logger.warning(
                "[MCPSyncService] Bot MCP override 当前不可投影: "
                "bot_id=%s, server_code=%s, error=%s",
                bot_id,
                server_code,
                verdict.get("error"),
            )
            return False
        current_detail = verdict.get("mcp_data")
        if isinstance(current_detail, dict):
            # Validate and compose from one Center snapshot. Otherwise a
            # second, older detail carried by the caller could reintroduce
            # the endpoint shape the validation just rejected/replaced.
            mcp_data = current_detail

    # 配置合并需读 DB；放入线程池，避免阻塞并发投递的 event loop。
    user_config = await asyncio.to_thread(config_service.get_user_unified_config, user_id, server_code)
    _api_key, merged_headers, _endpoint_env, _transport_protocol = await asyncio.to_thread(
        config_service.build_mcp_sync_payload,
        user_id=user_id,
        bot_id=bot_id,
        mcp_data=mcp_data,
        api_key=api_key,
        custom_headers=custom_headers,
        endpoint_env=endpoint_env,
        transport_protocol=transport_protocol,
        engine_type=engine_type,
        bot_override=bot_override,
        user_config_snapshot=user_config or {},
    )
    url_override = effective_mcp_url_override(user_config, bot_override)
    strict_transport_protocol = bool(
        bot_override
        and _transport_protocol
        and (
            "endpoint_env" in bot_override
            or "transport_protocol" in bot_override
        )
    )

    # 同步 HTTP 投递在线程池执行，避免阻塞事件循环；插件内部处理重试。
    return await asyncio.to_thread(
        plugin.sync_single_mcp,
        mcp_data,
        api_key=_api_key,
        custom_headers=merged_headers,
        endpoint_env=_endpoint_env,
        transport_protocol=_transport_protocol,
        url_override=url_override,
        strict_transport_protocol=strict_transport_protocol,
    )
