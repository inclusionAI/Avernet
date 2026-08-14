"""MCP 用户配置管理 —— 纯配置层，不涉及 HTTP。"""
from __future__ import annotations

import json
from typing import Any, Optional

from injector import inject

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.bot import UserMCPConfigRepository
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin
from agentclaw.community.log import get_logger

logger = get_logger()


class MCPConfigService:
    """管理用户级 MCP 配置（CRUD + 负载构建）。

    **不**向设备发送 HTTP 请求 —— 该职责由 ``MCPSyncService`` /
    ``DeviceMCPSyncPlugin`` 承担。
    """

    @inject
    def __init__(
        self,
        user_mcp_config_repo: UserMCPConfigRepository,
        mcp_center: MCPCenterPlugin,
        bot_repo: BotRepository,
    ) -> None:
        self.user_mcp_config_repo = user_mcp_config_repo
        self.mcp_center = mcp_center
        self._bot_repo = bot_repo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_user_unified_config(
        self, user_id: str, server_code: str
    ) -> Optional[dict[str, Any]]:
        """获取用户统一配置：api_key、headers、endpoint_env、transport_protocol。"""
        config = self.user_mcp_config_repo.get_by_user_and_server_code(
            user_id, server_code
        )
        if not config:
            return None

        # extra_config 在部分存储后端中以 JSON 字符串形式存放，需兼容处理
        extra_config = config.get("extra_config") or {}
        if isinstance(extra_config, str):
            try:
                extra_config = json.loads(extra_config)
            except json.JSONDecodeError:
                extra_config = {}
        if not isinstance(extra_config, dict):
            extra_config = {}

        api_key = extra_config.get("api_key") or config.get("api_key")
        headers = extra_config.get("headers", {})
        if isinstance(headers, str):
            try:
                headers = json.loads(headers)
            except json.JSONDecodeError:
                headers = {}

        return {
            "api_key": api_key,
            "headers": headers,
            "endpoint_env": extra_config.get("endpoint_env", "PROD"),
            "transport_protocol": extra_config.get("transport_protocol"),
        }

    def validate_headers_for_mcp(
        self, server_code: str, headers: dict[str, str]
    ) -> dict[str, Any]:
        """对 headers 做语法校验；暂不校验是否符合 MCP 服务端要求。"""
        mcp_data = self.mcp_center.get_mcp_detail(server_code)
        if not mcp_data:
            return {"valid": False, "error": "MCP 服务不存在"}

        for key, value in headers.items():
            if not key or not key.strip():
                return {"valid": False, "error": "Header 键不能为空"}
            if len(key) > 256:
                return {
                    "valid": False,
                    "error": f"Header 键过长: {key[:20]}...",
                }
            if value and len(value) > 2000:
                return {
                    "valid": False,
                    "error": f"Header 值过长，键: {key}",
                }

        return {"valid": True, "error": None}

    def update_user_unified_config(
        self,
        *,
        user_id: str,
        server_code: str,
        api_key: Optional[str],
        headers: Optional[dict[str, str]],
        endpoint_env: Optional[str],
        transport_protocol: Optional[str],
    ) -> dict[str, Any] | None:
        """新增或更新用户 MCP 配置，并返回旧配置供回滚使用。

        返回之前的统一配置（若不存在则返回 None）。
        """
        # 先读出旧配置，供 router 层在推送失败时做事务回滚。
        old_config = self.get_user_unified_config(user_id, server_code)

        from agentclaw.community.utils.env_utils import get_current_env

        existing = self.user_mcp_config_repo.get_by_user_and_server_code(
            user_id, server_code
        )
        if existing:
            # 合并策略：入参为 None 表示不修改，沿用旧值
            old_extra = old_config or {}
            extra_config = {
                "api_key": api_key if api_key is not None else old_extra.get("api_key"),
                "headers": headers if headers is not None else old_extra.get("headers", {}),
                "endpoint_env": endpoint_env
                if endpoint_env is not None
                else old_extra.get("endpoint_env", "PROD"),
                "transport_protocol": transport_protocol
                if transport_protocol is not None
                else old_extra.get("transport_protocol"),
            }
            self.user_mcp_config_repo.update(existing["id"], {
                "extra_config": extra_config,
                "api_key": extra_config["api_key"],
            })
        else:
            # 首次创建：None 字段写入默认值
            extra_config = {
                "api_key": api_key,
                "headers": headers if headers is not None else {},
                "endpoint_env": endpoint_env if endpoint_env is not None else "PROD",
                "transport_protocol": transport_protocol,
            }
            self.user_mcp_config_repo.create({
                "user_id": user_id,
                "server_code": server_code,
                "api_key": extra_config["api_key"],
                "extra_config": extra_config,
                "env": get_current_env(),
            })

        return old_config

    def rollback_unified_config(
        self,
        *,
        user_id: str,
        server_code: str,
        old_config: Optional[dict[str, Any]],
    ) -> None:
        """回滚到旧配置。若 old_config 为 None，则删除该记录。"""
        if old_config:
            self.update_user_unified_config(
                user_id=user_id,
                server_code=server_code,
                api_key=old_config.get("api_key"),
                headers=old_config.get("headers", {}),
                endpoint_env=old_config.get("endpoint_env", "PROD"),
                transport_protocol=old_config.get("transport_protocol"),
            )
        else:
            existing = self.user_mcp_config_repo.get_by_user_and_server_code(
                user_id, server_code
            )
            if existing:
                self.user_mcp_config_repo.delete(existing["id"])

    def build_mcp_sync_payload(
        self,
        *,
        user_id: str,
        mcp_data: dict[str, Any],
        api_key: Optional[str] = None,
        custom_headers: Optional[dict[str, str]] = None,
        endpoint_env: Optional[str] = None,
        transport_protocol: Optional[str] = None,
        engine_type: Optional[str] = None,
    ) -> tuple[Optional[str], dict[str, str], str, Optional[str]]:
        """根据用户配置与默认值构建合并后的 MCP 同步参数。

        返回 (api_key, merged_headers, endpoint_env, transport_protocol)。
        """
        server_code = mcp_data.get("serverCode") or mcp_data.get("server_code", "")
        _api_key = api_key
        _endpoint_env = endpoint_env or "PROD"
        _transport_protocol = transport_protocol
        extra_config: dict[str, Any] = {}

        # 用户自定义配置优先级高于默认值：先查用户是否写过该 MCP 的配置。
        user_mcp_config = self.user_mcp_config_repo.get_by_user_and_server_code(
            user_id, server_code
        )
        if user_mcp_config:
            extra = user_mcp_config.get("extra_config", {})
            if isinstance(extra, str):
                try:
                    extra = json.loads(extra)
                except json.JSONDecodeError:
                    extra = {}
            extra_config = extra if isinstance(extra, dict) else {}
            # 入参显式传了值则用入参，否则 fallback 到用户库里的配置。
            if _api_key is None and "api_key" in extra_config:
                _api_key = extra_config.get("api_key")
            if endpoint_env is None and "endpoint_env" in extra_config:
                _endpoint_env = extra_config.get("endpoint_env", "PROD")
            if _transport_protocol is None and "transport_protocol" in extra_config:
                _transport_protocol = extra_config.get("transport_protocol")

        from agentclaw.community.core.mcp.services._defaults import get_default_mcp_servers

        # 按引擎类型取默认 MCP 配置（如默认 headers）。
        effective_engine = engine_type or "openclaw"
        default_mcp_configs = get_default_mcp_servers(effective_engine)
        config_headers: dict[str, str] = {}
        for config in default_mcp_configs:
            if config.get("server_code") == server_code and "headers" in config:
                config_headers = config["headers"]
                break

        user_headers = custom_headers if custom_headers is not None else extra_config.get("headers", {})

        # 当 api_key 是 x-ling-auth 格式时，需要把默认 headers 里的同名 key 删掉，
        # 否则设备端会收到两个冲突的 authorization header。
        if _api_key and "=" in _api_key:
            key_name, _ = _api_key.split("=", 1)
            if key_name.lower() == "x-ling-auth":
                config_headers = {
                    k: v for k, v in config_headers.items() if k.lower() != "x-ling-auth"
                }

        # 合并策略：默认 headers 作为基底，用户 headers 覆盖同名键。
        merged_headers = {**config_headers, **user_headers}

        return _api_key, merged_headers, _endpoint_env, _transport_protocol
