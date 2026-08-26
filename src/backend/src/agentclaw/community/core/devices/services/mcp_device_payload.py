"""Transport-neutral MCP payload shaping for device delivery.

Only non-sensitive metadata is logged; tokens, authorization headers, cookies,
and complete MCP configurations are never written to logs.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from agentclaw.community.log import get_logger

logger = get_logger()

# 重试配置
MAX_RETRY_ATTEMPTS = 3  # 最大重试次数
RETRY_BACKOFF_BASE = 1  # 退避基数（1s, 2s, 4s）


class DeviceMCPConfig:
    """MCP Server 配置（符合远端设备接口）"""

    def __init__(
            self,
            server_code: str,
            transport: str = "sse",
            url: Optional[str] = None,
            command: Optional[str] = None,
            args: Optional[List[str]] = None,
            env: Optional[Dict[str, str]] = None,
            headers: Optional[Dict[str, str]] = None,
            timeout_seconds: int = 30,
            enabled: bool = True,
            description: Optional[str] = None,
    ):
        self.server_code = server_code
        self.transport = transport
        self.url = url
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.headers = headers or {}
        self.timeout_seconds = timeout_seconds
        self.enabled = enabled
        self.description = description

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "server_code": self.server_code,
            "description": self.description or "",
            "transport": self.transport,
            "args": self.args,
            "env": self.env,
            "headers": self.headers,
            "timeout_seconds": self.timeout_seconds,
            "enabled": self.enabled,
            # mcporter requires command and url to be string/array, not null
            # Adapter saves null values to config which breaks mcporter validation
            "command": self.command or "",
            "url": self.url or "",
        }
        return result


def is_already_exists_error(e: Exception) -> bool:
    msg = str(e)
    if "409" in msg:
        return True
    if "已存在" in msg:
        return True
    if "already exists" in msg.lower():
        return True
    return False


def convert_to_device_format(
        mcp_data: Dict[str, Any],
        api_key: Optional[str] = None,
        custom_headers: Optional[Dict[str, str]] = None,
        endpoint_env: str = "PROD",
        transport_protocol: Optional[str] = None,
) -> DeviceMCPConfig:
    """将 MCP market 数据转换为设备格式"""
    server_code = mcp_data.get("server_code") or mcp_data.get("serverCode")
    description = mcp_data.get("description") or mcp_data.get("name")

    headers = {}
    url_api_key = None
    if api_key and "=" in api_key:
        key_name, key_value = api_key.split("=", 1)
        if key_name.lower() == "authorization":
            url_api_key = (key_name, key_value)
            logger.info("[convert_to_device_format] Will add authorization to URL for LING_XI MCP %s", server_code)
        elif key_name.lower() == "x-ling-auth":
            headers[key_name] = key_value
            logger.info("[convert_to_device_format] Adding header '%s' for LING_XI MCP %s", key_name, server_code)

    if custom_headers:
        headers.update(custom_headers)
        logger.info("[convert_to_device_format] Added %s custom headers for MCP %s", len(custom_headers), server_code)

    run_mode = mcp_data.get("run_mode") or mcp_data.get("runMode", "REMOTE")
    endpoints = mcp_data.get("endpoints", [])
    stdio_configs = mcp_data.get("stdio_configs", []) or mcp_data.get("stdioConfigs", [])

    if isinstance(endpoints, str):
        try:
            endpoints = json.loads(endpoints)
        except json.JSONDecodeError:
            endpoints = []

    if isinstance(stdio_configs, str):
        try:
            stdio_configs = json.loads(stdio_configs)
        except json.JSONDecodeError:
            stdio_configs = []

    if run_mode == "LOCAL" and stdio_configs:
        stdio = stdio_configs[0] if stdio_configs else {}
        config = DeviceMCPConfig(
            server_code=server_code,
            transport="stdio",
            command=stdio.get("command"),
            args=stdio.get("arguments", []),
            env=stdio.get("envVariables", {}),
            headers=headers,
            enabled=True,
            description=description,
        )
        # AC-18: log only server_code — never the full config object (may carry secrets).
        logger.info("[convert_to_device_format] LOCAL mode final config for %s", server_code)
        return config
    else:
        url = None
        transport = "sse"

        if endpoints:
            valid_endpoints = [
                ep for ep in endpoints
                if ep.get("networkType") in ("OFFICE", "INTERNET") and ep.get("env") == endpoint_env
            ]
            if not valid_endpoints:
                raise Exception(f"MCP服务器 {server_code} 没有可用的{endpoint_env}环境端点(OFFICE/INTERNET网络)，请检查MCP Center配置")

            ep = None
            if transport_protocol:
                for candidate in valid_endpoints:
                    if candidate.get("transportProtocol") == transport_protocol:
                        ep = candidate
                        break
                if ep is None:
                    logger.warning("[convert_to_device_format] User preferred %s not available for %s, falling back to first available endpoint", transport_protocol, server_code)
                    ep = valid_endpoints[0]
            else:
                for candidate in valid_endpoints:
                    if candidate.get("transportProtocol") == "STREAMABLE_HTTP":
                        ep = candidate
                        break
                if ep is None:
                    ep = valid_endpoints[0]

            url = ep.get("url")
            protocol = ep.get("transportProtocol", "SSE")
            transport = "http" if protocol == "STREAMABLE_HTTP" else "sse"

            logger.info("[convert_to_device_format] Selected %s endpoint for %s: %s (protocol=%s)", endpoint_env, server_code, url, protocol)

        if url and url_api_key:
            key_name, key_value = url_api_key
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{key_name}={key_value}"
            logger.info("[convert_to_device_format] Added authorization to URL for %s", server_code)

        config = DeviceMCPConfig(
            server_code=server_code,
            transport=transport,
            url=url,
            headers=headers,
            enabled=True,
            description=description,
        )
        # AC-18: log only server_code — never the full config object (may carry secrets).
        logger.info("[convert_to_device_format] REMOTE mode final config for %s", server_code)
        return config