"""Transport-agnostic MCP delivery helpers for DeviceSync services.

The helpers operate only on an injected transport and contain no concrete HTTP
client construction, so BaaS and provider-specific DeviceSync services can
share payload, retry, and result behavior.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.http_client import (
    HttpClientRequestError,
)
from agentclaw.community.core.devices.services.mcp_device_payload import (
    MAX_RETRY_ATTEMPTS,
    RETRY_BACKOFF_BASE,
    DeviceMCPConfig,
    convert_to_device_format,
    is_already_exists_error,
)

logger = get_logger()

_TIMEOUT = 30  # seconds


def mcp_base_url_from_conn_info(conn_info: Dict[str, Any]) -> str:
    """Return the ``.../api/mcp`` base URL for a device's engine from its conn_info.

    Ported from the former ``ProdDeviceMCPSyncPlugin._get_base_url`` for callers
    that hold a raw conn_info dict (test/integration harnesses). The per-bot
    ``DeviceSync`` impls don't use this — Arca/Baas each compute their own
    base from construction-time state.

    - Arca / local: ``{conn_info['url']}/api/mcp``
    - BaaS: ``{baas_base_url}/api/v1/bots/{tenant}/{bot_uuid}/invoke-http/{port}/api/mcp``
    """
    if conn_info.get("device_provider") == "baas":
        baas_base_url = conn_info["baas_base_url"]
        tenant = conn_info.get("tenant", "")
        bot_uuid = conn_info["paas_device_id"]
        engine_port = conn_info["engine_port"]
        base = (
            f"{baas_base_url}/api/v1/bots/{tenant}/"
            f"{bot_uuid}/invoke-http/{engine_port}"
        )
    else:
        base = conn_info["url"]
    return f"{base}/api/mcp"


_MCP_PATH = "/api/mcp"


def probe_mcp(
        transport: Any,
        server_code: str,
) -> bool:
    """探测设备引擎，确认 MCP 是否已安装（``GET /api/mcp/{server_code}`` == 200）。"""
    try:
        response = transport.get(f"{_MCP_PATH}/{server_code}")
        return response.status_code == 200
    except Exception as e:
        logger.warning("[mcp_transport] 探测设备上的 MCP %s 失败: %s", server_code, e)
        return False


def filter_servers(
        transport: Any,
        mcp_servers: List[Dict[str, Any]],
) -> bool:
    """全量同步：声明当前 skillset 所用的全部 MCP 到设备（filter-servers）。"""
    try:
        def get_server_code(mcp):
            return mcp.get("server_code") or mcp.get("serverCode")

        server_codes = [get_server_code(s) for s in mcp_servers if get_server_code(s)]

        response = None
        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                response = transport.post(
                    f"{_MCP_PATH}/filter-servers",
                    json={"server_codes": server_codes},
                )
                if response.status_code < 500:
                    break
                # 带容器 detail，便于定位（如 mcporter cwd deleted）。
                raise Exception(f"Server error: {response.status_code}, {response.text}")
            except Exception as e:
                if attempt == MAX_RETRY_ATTEMPTS - 1:
                    raise
                wait = RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.warning("[filter_servers] Retry filter-servers in %ss: %s", wait, e)
                time.sleep(wait)

        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                logger.info("[mcp_transport] Declared %s MCP servers to device", len(server_codes))
                return True
            else:
                logger.error("[mcp_transport] filter-servers failed: %s", result.get("message"))
                return False
        else:
            logger.error("[mcp_transport] filter-servers error: %s, %s", response.status_code, response.text)
            return False

    except HttpClientRequestError as e:
        logger.exception("[mcp_transport] Failed to sync all MCPs: %s", e)
        return False
    except Exception as e:
        logger.exception("[mcp_transport] Unexpected error during sync all: %s", e)
        return False


def push_single_mcp(
        transport: Any,
        mcp_data: Dict[str, Any],
        *,
        api_key: Optional[str] = None,
        custom_headers: Optional[Dict[str, str]] = None,
        endpoint_env: str = "PROD",
        transport_protocol: Optional[str] = None,
) -> bool:
    """增量同步：将单个 MCP 添加（或更新）到设备。"""
    try:
        config = convert_to_device_format(
            mcp_data, api_key=api_key, custom_headers=custom_headers,
            endpoint_env=endpoint_env, transport_protocol=transport_protocol,
        )

        try:
            _create_mcp(transport, config)
            logger.info("[mcp_transport] Created MCP %s on device", config.server_code)
        except Exception as e:
            if is_already_exists_error(e):
                logger.info("[mcp_transport] MCP %s already exists, updating...", config.server_code)
                _update_mcp(transport, config)
                logger.info("[mcp_transport] Updated MCP %s on device", config.server_code)
            else:
                raise

        return True

    except HttpClientRequestError as e:
        logger.exception("[mcp_transport] Failed to sync single MCP: %s", e)
        sc = mcp_data.get("server_code") or mcp_data.get("serverCode")
        raise Exception(f"Network error syncing {sc}: {e}") from e
    except Exception as e:
        logger.exception("[mcp_transport] Unexpected error during sync single: %s", e)
        raise


def remove_mcp(
        transport: Any,
        server_code: str,
) -> bool:
    """从设备删除单个 MCP 配置。"""
    try:
        _delete_mcp(transport, server_code)
        logger.info("[mcp_transport] Removed MCP %s from device", server_code)
        return True
    except Exception as e:
        logger.error("[mcp_transport] Failed to remove MCP %s from device: %s", server_code, e)
        return False


def _create_mcp(transport: Any, config: DeviceMCPConfig):
    """Create MCP with retry (409 不重试，直接抛出)."""
    for attempt in range(MAX_RETRY_ATTEMPTS):
        try:
            response = transport.post(_MCP_PATH, json=config.to_dict())
            if response.status_code in [200, 201]:
                return
            if response.status_code == 409:
                raise Exception(f"Create failed: 409, {response.text}")
            raise Exception(f"Create failed: {response.status_code}, {response.text}")
        except Exception as e:
            if is_already_exists_error(e):
                raise
            if attempt == MAX_RETRY_ATTEMPTS - 1:
                raise Exception(f"Create MCP failed after {MAX_RETRY_ATTEMPTS} attempts: {e}")
            wait = RETRY_BACKOFF_BASE * (2 ** attempt)
            logger.warning("[_create_mcp] Retry in %ss for %s: %s", wait, config.server_code, e)
            time.sleep(wait)


def _update_mcp(transport: Any, config: DeviceMCPConfig):
    """Update MCP with retry."""
    path = f"{_MCP_PATH}/{config.server_code}"
    for attempt in range(MAX_RETRY_ATTEMPTS):
        try:
            response = transport.put(path, json=config.to_dict())
            if response.status_code in [200, 201]:
                return
            raise Exception(f"Update failed: {response.status_code}, {response.text}")
        except Exception as e:
            if attempt == MAX_RETRY_ATTEMPTS - 1:
                raise Exception(f"Update MCP failed after {MAX_RETRY_ATTEMPTS} attempts: {e}")
            wait = RETRY_BACKOFF_BASE * (2 ** attempt)
            logger.warning("[_update_mcp] Retry in %ss for %s: %s", wait, config.server_code, e)
            time.sleep(wait)


def _delete_mcp(transport: Any, server_code: str):
    """Delete MCP with retry."""
    path = f"{_MCP_PATH}/{server_code}"
    for attempt in range(MAX_RETRY_ATTEMPTS):
        try:
            response = transport.delete(path)
            if response.status_code in [200, 204]:
                return
            if response.status_code == 404:
                return
            raise Exception(f"Delete failed: {response.status_code}, {response.text}")
        except Exception as e:
            if attempt == MAX_RETRY_ATTEMPTS - 1:
                logger.error("[_delete_mcp] Failed after %s attempts: %s", MAX_RETRY_ATTEMPTS, e)
                return
            wait = RETRY_BACKOFF_BASE * (2 ** attempt)
            logger.warning("[_delete_mcp] Retry in %ss for %s: %s", wait, server_code, e)
            time.sleep(wait)