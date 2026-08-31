"""Shared Core DeviceSync service for BaaS-backed devices.

The service contains provider business logic only. A ``BaasTransport`` is
injected by the active profile's composition root, allowing community,
singlebox, corp, desktop, and test profiles to share the same DeviceSync
behavior without making the service profile-aware.
"""
from __future__ import annotations

from typing import Any, Optional

from agentclaw.community.core.devices.services.baas_invoke_transport import BaasTransport

from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.http_client import (
    HttpClientRequestError,
    HttpClientStatusError,
)
from agentclaw.community.core.devices.services import mcp_device_transport as mcp_transport

logger = get_logger()


class BaasDeviceSyncService(DeviceSync):
    """Core :class:`DeviceSync` — business logic layer, transport injected.

    conn_info fields (日志/兼容用，MCP 与 symlinks 均走注入的 transport):
      - baas_base_url: str
      - paas_device_id: str
      - engine_port: int
      - tenant: str
      - headers: dict[str, str]
    """

    def __init__(
        self,
        *,
        transport: BaasTransport,
        conn_info: dict[str, Any],
    ):
        self._transport = transport
        # MCP delegation 用,自拼 base URL
        self._baas_base_url: str = conn_info.get("baas_base_url", "")
        self._bot_uuid: str = conn_info.get("paas_device_id", "")
        self._engine_port: int = conn_info["engine_port"]
        self._tenant: str = conn_info.get("tenant", "")
        self._headers: dict[str, str] = conn_info.get("headers", {})

    def sync_symlinks(
        self,
        symlinks: list[dict[str, Any]],
        *,
        effective_mcps: Optional[list[dict[str, Any]]] = None,
        desired_skills: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        # ``effective_mcps`` / ``desired_skills`` only mean something to a device that recomposes
        # its whole configuration; this one is told exactly what to link and
        # ignores it.
        symlinks_count = len(symlinks)
        logger.info(
            "[BaasDeviceSyncService.sync_symlinks] bot_uuid=%s, count=%d",
            self._bot_uuid, symlinks_count,
        )

        try:
            if symlinks_count == 0:
                body = {"directories": ["/home/admin/.openclaw/workspace/skills"]}
                response = self._transport.post(
                    "/api/skills/symlink/clean", json=body
                )
                response.raise_for_status()
                return {
                    "success": True,
                    "message": "同步成功",
                    "data": response.json(),
                }

            valid_symlinks = self._ensure_center_skills(symlinks)

            body = {
                "symlinks": [
                    {"source": s["source"], "target": s["target"]}
                    for s in valid_symlinks
                ],
                "clean_target_dir": True,
            }
            response = self._transport.post(
                "/api/skills/symlink/bindpath", json=body
            )
            response.raise_for_status()
            return {
                "success": True,
                "message": "同步成功",
                "data": response.json(),
            }
        except HttpClientStatusError as e:
            logger.error(
                "[BaasDeviceSyncService.sync_symlinks] HTTP %d: %s",
                e.response.status_code, e.response.text,
            )
            return {"success": False, "message": f"HTTP 错误: {e.response.status_code}"}
        except HttpClientRequestError as e:
            logger.error("[BaasDeviceSyncService.sync_symlinks] request failed: %s", e)
            return {"success": False, "message": f"请求失败: {e}"}
        except Exception as e:
            logger.exception("[BaasDeviceSyncService.sync_symlinks] error: %s", e)
            return {"success": False, "message": f"同步失败: {e}"}

    def sync_bot_config(
        self,
        bot_id: str,
        binding_id: int,
        public: str,
        permission_owner: "str | None",
        user_id: str,
        nick_name: str,
    ) -> dict[str, Any]:
        """POST ``{role, visibility}`` to ``/api/bot/config`` over injected transport."""
        config_data: dict[str, str] = {}
        if permission_owner:
            config_data["role"] = permission_owner.upper()
        config_data["visibility"] = "PUBLIC" if public == "1" else "PRIVATE"

        if not config_data:
            logger.warning(
                "[BaasDeviceSyncService.sync_bot_config] bot=%s: no valid config to sync",
                bot_id,
            )
            return {"success": False, "message": "No valid config to sync"}

        if not binding_id:
            logger.warning(
                "[BaasDeviceSyncService.sync_bot_config] bot=%s: no binding_id, skip sync",
                bot_id,
            )
            return {"success": False, "message": "Bot has no device binding"}

        try:
            response = self._transport.post("/api/bot/config", json=config_data)
            response.raise_for_status()
            result = response.json()
            logger.info(
                "[BaasDeviceSyncService.sync_bot_config] bot=%s binding_id=%s config=%s response=%s",
                bot_id, binding_id, config_data, result,
            )
            return {
                "success": True,
                "message": result.get("message", "更新成功"),
                "data": result.get("data"),
            }
        except HttpClientStatusError as e:
            logger.error(
                "[BaasDeviceSyncService.sync_bot_config] bot=%s HTTP error: %s - %s",
                bot_id, e.response.status_code, e.response.text,
            )
            return {
                "success": False,
                "message": f"HTTP 错误: {e.response.status_code}",
            }
        except HttpClientRequestError as e:
            logger.error(
                "[BaasDeviceSyncService.sync_bot_config] bot=%s request failed: %s",
                bot_id, e,
            )
            return {"success": False, "message": f"请求失败: {e}"}

    # ── MCP delegation (走注入的 transport，与 symlinks 同款平台分流) ─────
    def sync_all_mcp_servers(self, mcp_servers: list[dict[str, Any]]) -> bool:
        return mcp_transport.filter_servers(self._transport, mcp_servers)

    def sync_single_mcp(
        self,
        mcp_data: dict[str, Any],
        *,
        api_key: "str | None" = None,
        custom_headers: "dict[str, str] | None" = None,
        endpoint_env: str = "PROD",
        transport_protocol: "str | None" = None,
    ) -> bool:
        return mcp_transport.push_single_mcp(
            self._transport, mcp_data,
            api_key=api_key, custom_headers=custom_headers,
            endpoint_env=endpoint_env, transport_protocol=transport_protocol,
        )

    def sync_remove_mcp(self, server_code: str) -> bool:
        return mcp_transport.remove_mcp(self._transport, server_code)

    def has_mcp(self, server_code: str) -> bool:
        return mcp_transport.probe_mcp(self._transport, server_code)

    def _ensure_center_skills(
        self, symlinks: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """No-op for BAAS device(skills-repo 通常是 RO mount)。"""
        logger.info(
            "[BaasDeviceSyncService._ensure_center_skills] BAAS device, skipping ensure"
        )
        return list(symlinks)
