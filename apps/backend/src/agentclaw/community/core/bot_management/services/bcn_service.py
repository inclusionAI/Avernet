"""BCN Service - Bot Coordination Network 服务接口。

负责与 BCN (Bot Coordination Network) 服务交互，包括：
- Bot 入网注册 (onboard, 上行)
- Bot 信息同步 (name, summary)
- Bot create/start 时的 Provider Bot 注册 (下行, 见 register_provider_bot)

Mechanical split note: the task-mode roster cache/list members moved verbatim
to ``bcn_roster.BcnRosterMixin`` (composed below), and the typed transport
errors plus the shared provider-config seam moved to
``bcn_transport_types``. This module stays the single public import path and
re-exports those names so every historical
``from ...bcn_service import X`` keeps working.
"""

import threading
import time
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Mapping, Optional

import httpx

from agentclaw.community.kernel.bot_manager_sync import DeliveryFailure
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.http_client import HttpClient
from agentclaw.community.utils.env_utils import get_current_env

from agentclaw.community.core.bot_management.services.bcn_roster import BcnRosterMixin
# Typed transport definitions (moved out of this module and re-exported from
# here so historical ``from ...bcn_service import BcnServiceError`` keeps
# working).
from agentclaw.community.core.bot_management.services.bcn_transport_types import (
    BcnBotMissingError,
    BcnRequestError,
    BcnServiceError,
    BcnUnavailableError,
    _get_provider_config,
    bcn_http_error,
)

if TYPE_CHECKING:
    from agentclaw.community.di.config import BcnConfig

logger = get_logger()


def _get_header(headers: Mapping[str, str], name: str) -> Optional[str]:
    """Read a header from a plain mapping using HTTP case-insensitive semantics."""
    value = headers.get(name) or headers.get(name.lower()) or headers.get(name.upper())
    if value:
        return value

    lower_name = name.lower()
    for key, candidate in headers.items():
        if key.lower() == lower_name and candidate:
            return candidate
    return None


def _build_auth_headers(request_headers: Optional[Mapping[str, str]]) -> Dict[str, str]:
    """Select the minimal incoming auth headers that BCN identity extraction needs."""
    if not request_headers:
        return {}

    auth_headers: Dict[str, str] = {}
    cookie = _get_header(request_headers, "Cookie")
    if cookie:
        auth_headers["Cookie"] = cookie

    authorization = _get_header(request_headers, "Authorization")
    if authorization:
        auth_headers["Authorization"] = authorization

    return auth_headers


class BcnService(BcnRosterMixin):
    """BCN 服务 - 与 Bot Coordination Network 交互。

    负责调用 BCN 的 HTTP API，包括：
    - POST /admin/bots/onboard - Bot 入网/更新信息
    """

    def __init__(
        self,
        http_client: HttpClient,
        config: "BcnConfig | None" = None,
        timeout: float = 30.0,
    ):
        """初始化 BCN 服务。

        Args:
            http_client: bcn-qualified :class:`HttpClient` (its ``base_url`` is the
                BCN host); all requests use relative paths.
            config: :class:`BcnConfig` — provider credentials for the down-link.
                Defaults to an empty config (no provider ⇒ registration skips).
            timeout: 请求超时时间（秒）
        """
        from agentclaw.community.di.config import BcnConfig

        self._http = http_client
        self._config = config if config is not None else BcnConfig()
        self._timeout = timeout
        self._roster_cache: dict[tuple, tuple] = {}
        self._roster_cache_lock = threading.Lock()
        logger.info("[BcnService] Initialized")

    def _resolve_send_timeout(self, override: float | None) -> float:
        """单次发送超时：显式传入剩余预算(remaining)时本次请求使用之，``None``
        沿用服务级 ``self._timeout`` 既有行为。"""
        return override if override is not None else self._timeout

    def onboard_bot(
        self,
        bot_id: str,
        name: str,
        summary: str,
        hidden: bool = False,
        request_headers: Optional[Mapping[str, str]] = None,
        *,
        avatar_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> Dict[str, Any]:
        """Bot 入网注册或更新信息。

        调用 BCN 的 /admin/bots/onboard 接口。
        - 如果 bot_id 不存在，则创建新的 Bot 记录
        - 如果 bot_id 已存在，则更新 name 和 summary

        Args:
            bot_id: Bot 唯一标识，格式为 "{tc_bot_id}:{owner_workno}"
                    例如："20260421_gfdsz5vi:85020"
            name: Bot 显示名称
            summary: Bot 简介
            hidden: 是否隐藏（默认 False）
            request_headers: 原始 HTTP 请求头；BCN 层仅透传 Cookie / Authorization
            avatar_url: 头像 URL；``None`` 在这个既有可省略输入边界表示"不发送
                该字段"（payload 与旧调用完全一致），空字符串 ``""`` 显式发送以
                清空远端头像，非空值原样发送。
            timeout_seconds: 单次发送的剩余超时预算（秒）；``None`` 沿用服务级
                ``self._timeout``。

        Returns:
            BCN 返回的结果，包含：
            - bot_uuid: Bot UUID
            - onboarded: 是否已入网 (``onboarded=false`` 原样返回，由调用方解释)
            - name: Bot 名称

        Raises:
            BcnServiceError: 调用 BCN 接口失败 (404/503/4xx/5xx/超时会进一步
                细分为 :class:`BcnBotMissingError` / :class:`BcnUnavailableError`
                / :class:`BcnRequestError`)
        """
        logger.info(
            f"[BcnService.onboard_bot] Calling BCN onboard: "
            f"bot_id={bot_id}, name={name}, summary={summary[:50]}..., hidden={hidden}"
        )

        # Step 1: Call BCN onboard API

        payload: Dict[str, Any] = {
            "bot_id": bot_id,
            "name": name,
            "summary": summary,
        }
        if hidden:
            payload["hidden"] = True
        if avatar_url is not None:
            payload["avatar_url"] = avatar_url

        try:
            auth_headers = _build_auth_headers(request_headers)
            response = self._http.post(
                "/admin/bots/onboard",
                json=payload,
                headers=auth_headers or None,
                timeout=self._resolve_send_timeout(timeout_seconds),
            )
            response.raise_for_status()

            response_data = response.json()

            # BCN 接口返回格式：{"bot_uuid": "xxx", "onboarded": true, "name": "xxx"}
            # 或者错误格式：{"error": "xxx"} 或 {"message": "xxx"}
            if "error" in response_data:
                raise BcnServiceError(f"BCN API error: {response_data.get('error')}")

            bot_uuid = response_data.get("bot_uuid")
            onboarded = response_data.get("onboarded", False)

            logger.info(
                f"[BcnService.onboard_bot] BCN onboard succeeded: "
                f"bot_id={bot_id}, bot_uuid={bot_uuid}, onboarded={onboarded}"
            )

            return response_data

        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:500] if e.response else "No response"
            logger.error(
                f"[BcnService.onboard_bot] HTTP error: "
                f"status={e.response.status_code if e.response else 'N/A'}, "
                f"body={error_body}"
            )
            raise bcn_http_error(
                "BCN API HTTP error",
                e.response.status_code if e.response else None,
            )
        except httpx.TimeoutException as e:
            logger.error(f"[BcnService.onboard_bot] Timeout error: {e}")
            raise BcnRequestError(
                DeliveryFailure.UNKNOWN, "timeout", f"BCN API timeout: {e}"
            )
        except BcnServiceError:
            raise
        except Exception as e:
            logger.error(f"[BcnService.onboard_bot] Unexpected error: {e}")
            raise BcnRequestError(
                DeliveryFailure.UNKNOWN, "unknown", f"BCN API error: {e}"
            )

    def register_provider_bot(
        self,
        *,
        teamclaw_bot_uuid: str,
        owner_workno: str,
        name: str,
        summary: str,
        connection_mode: Literal["plugin"] | None = None,
        provider_bot_ref: str | None = None,
        avatar_url: str | None = None,
    ) -> Dict[str, Any]:
        """将 bot 注册为 Provider bot (下行链路)。

        语雀: doc 548864073, POST /providers/{provider_id}/bots

        Args:
            provider_bot_ref: Stable service address supplied from a resolved Bot.
                Omission preserves legacy Owner-qualified personal/desktop calls.
            teamclaw_bot_uuid: TC 的 bot uuid (例如 "20260502_1cjjh1ik")
            owner_workno: bot 所有者工号 (例如 "100000")
            name: bot 显示名
            summary: bot 简介 (空字符串也可)
            connection_mode: 仅 OpenClaw personal 使用 ``plugin``；未传时由 BCN
                按既有 ``gateway`` 默认处理。
            avatar_url: 头像 URL；``None`` 在这个既有可省略输入边界表示"不发送
                该字段"（payload 与旧调用完全一致），空字符串 ``""`` 显式发送以
                清空远端头像，非空值原样发送。

        Returns:
            BCN 返回的 dict, 关键字段:
              - bot_uuid
              - provider_id
              - provider_bot_ref (explicit stable reference, or legacy Owner-qualified default)
              - bot_runtime_token (后续下行调用凭据)
            额外字段 (本方法附加):
              - skipped: dev 环境跳过时为 True
              - idempotent_replay: 409 已注册时为 True

        Raises:
            BcnServiceError: 4xx/5xx (除 409) / 超时 / 其他网络异常
        """
        if provider_bot_ref is None:
            provider_bot_ref = f"{teamclaw_bot_uuid}:{owner_workno}"
        elif not provider_bot_ref.strip() or not provider_bot_ref.startswith(f"{teamclaw_bot_uuid}:"):
            raise BcnServiceError("Invalid provider_bot_ref")
        env = get_current_env()

        # dev / local 环境无 provider 凭据, 跳过实际调用 (排查日志关键字: [register_provider_bot])
        provider_cfg = _get_provider_config(env, self._config)
        if not provider_cfg:
            logger.info(
                f"[BcnService.register_provider_bot] env={env} skipped "
                f"(no provider credentials), provider_bot_ref={provider_bot_ref} "
                f"connection_mode={connection_mode}"
            )
            return {
                "bot_uuid": "",
                "provider_id": "",
                "provider_bot_ref": provider_bot_ref,
                "bot_runtime_token": "",
                "skipped": True,
            }

        provider_id = provider_cfg["provider_id"]
        provider_admin_token = provider_cfg["provider_admin_token"]

        owners: List[str] = [owner_workno]
        payload: Dict[str, Any] = {
            "name": name,
            "summary": summary,
            "owners": owners,
            "provider_bot_ref": provider_bot_ref,
        }
        if connection_mode is not None:
            payload["connection_mode"] = connection_mode
        if avatar_url is not None:
            payload["avatar_url"] = avatar_url
        path = f"/providers/{provider_id}/bots"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {provider_admin_token}",
        }
        # Project only documented business fields; runtime/admin tokens stay private.
        context = (
            f"system=BCN direction=outbound operation=register_provider_bot "
            f"method=POST path={path} provider_id={provider_id} "
            f"provider_bot_ref={provider_bot_ref} connection_mode={connection_mode}"
        )
        logger.info(
            "[BcnService.register_provider_bot] event=request %s payload=%s",
            context, payload,
        )
        started = time.monotonic()
        response = None
        try:
            response = self._http.post(
                path, json=payload, headers=headers, timeout=self._timeout
            )

            # 409 remains an idempotent success, including non-JSON responses.
            if response.status_code == 409:
                body: Dict[str, Any] = {}
                try:
                    body = response.json() or {}
                except ValueError:
                    body = {}
                body.setdefault("provider_id", provider_id)
                body.setdefault("provider_bot_ref", provider_bot_ref)
                body["idempotent_replay"] = True
            else:
                response.raise_for_status()
                body = response.json()

            logger.info(
                "[BcnService.register_provider_bot] event=success %s "
                "status=%s elapsed_ms=%.2f bot_uuid=%s name=%s summary=%s "
                "owners=%s idempotent_replay=%s runtime_token_present=%s",
                context, response.status_code, (time.monotonic() - started) * 1000,
                body.get("bot_uuid"), body.get("name"), body.get("summary"),
                body.get("owners"), bool(body.get("idempotent_replay")),
                bool(body.get("bot_runtime_token")),
            )
            return body

        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response else "N/A"
            logger.error(
                "[BcnService.register_provider_bot] event=failure %s "
                "status=%s elapsed_ms=%.2f error_type=%s reason=http_status_error",
                context, status, (time.monotonic() - started) * 1000,
                type(e).__name__,
            )
            raise bcn_http_error(
                "BCN register_provider_bot HTTP error",
                e.response.status_code if e.response else None,
            )
        except httpx.TimeoutException as e:
            logger.error(
                "[BcnService.register_provider_bot] event=failure %s "
                "status=timeout elapsed_ms=%.2f error_type=%s reason=request_timeout",
                context, (time.monotonic() - started) * 1000, type(e).__name__,
            )
            raise BcnRequestError(
                DeliveryFailure.UNKNOWN,
                "timeout",
                f"BCN register_provider_bot timeout: {e}",
            )
        except BcnServiceError:
            raise
        except Exception as e:
            logger.error(
                "[BcnService.register_provider_bot] event=failure %s "
                "status=%s elapsed_ms=%.2f error_type=%s reason=unexpected_error",
                context, response.status_code if response is not None else "unavailable",
                (time.monotonic() - started) * 1000, type(e).__name__,
            )
            raise BcnRequestError(
                DeliveryFailure.UNKNOWN,
                "unknown",
                f"BCN register_provider_bot error: {e}",
            )

    def switch_bot(
        self,
        *,
        teamclaw_bot_uuid: str,
        owner_workno: str,
        name: str,
        summary: str,
    ) -> Dict[str, Any]:
        """切换 Provider Bot 绑定。

        调用 BCN 的 POST /providers/{provider_id}/delivery/switch-bot 接口。
        用于切换 bot 的绑定关系，返回新的 token。

        Args:
            teamclaw_bot_uuid: TC 的 bot uuid (例如 "20260502_1cjjh1ik")
            owner_workno: bot 所有者工号 (例如 "100000")
            name: bot 显示名
            summary: bot 简介

        Returns:
            BCN 返回的 dict, 关键字段:
              - bot_id
              - provider_id
              - provider_bot_ref
              - token (新 token)
              - binding_created_at
              - idempotent_replay
              - websocket_kicked
            额外字段 (本方法附加):
              - skipped: dev 环境跳过时为 True

        Raises:
            BcnServiceError: 4xx/5xx / 超时 / 其他网络异常
        """
        provider_bot_ref = f"{teamclaw_bot_uuid}:{owner_workno}"
        env = get_current_env()

        # dev / local 环境无 provider 凭据, 跳过实际调用
        provider_cfg = _get_provider_config(env, self._config)
        if not provider_cfg:
            logger.info(
                f"[BcnService.switch_bot] env={env} skipped "
                f"(no provider credentials), provider_bot_ref={provider_bot_ref}"
            )
            return {
                "bot_id": "",
                "provider_id": "",
                "provider_bot_ref": provider_bot_ref,
                "token": "",
                "binding_created_at": 0,
                "idempotent_replay": False,
                "websocket_kicked": False,
                "skipped": True,
            }

        provider_id = provider_cfg["provider_id"]
        provider_admin_token = provider_cfg["provider_admin_token"]

        payload: Dict[str, Any] = {
            "bot_id": provider_bot_ref,
            "provider_bot_ref": provider_bot_ref,
            "name": name,
            "summary": summary,
        }
        path = f"/providers/{provider_id}/delivery/switch-bot"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {provider_admin_token}",
        }
        logger.info(
            f"[BcnService.switch_bot] POST {path} "
            f"provider_bot_ref={provider_bot_ref} name={name} "
            f"summary_len={len(summary or '')}"
        )

        try:
            response = self._http.post(
                path, json=payload, headers=headers, timeout=self._timeout
            )
            response.raise_for_status()
            response_data = response.json()

            # BCN 接口返回格式：{"success": true, "data": {...}}
            if not response_data.get("success"):
                error_msg = (
                    response_data.get("error")
                    or response_data.get("message")
                    or "Unknown error"
                )
                raise BcnServiceError(f"BCN switch_bot API error: {error_msg}")

            data = response_data.get("data", {})
            logger.info(
                f"[BcnService.switch_bot] OK "
                f"bot_id={data.get('bot_id')} "
                f"provider_bot_ref={provider_bot_ref} "
                f"websocket_kicked={data.get('websocket_kicked')}"
            )
            return data

        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:500] if e.response else "No response"
            status = e.response.status_code if e.response else "N/A"
            logger.error(
                f"[BcnService.switch_bot] HTTP error: "
                f"status={status} body={error_body} "
                f"provider_bot_ref={provider_bot_ref}"
            )
            raise bcn_http_error(
                "BCN switch_bot HTTP error",
                e.response.status_code if e.response else None,
            )
        except httpx.TimeoutException as e:
            logger.error(
                f"[BcnService.switch_bot] Timeout: {e} "
                f"provider_bot_ref={provider_bot_ref}"
            )
            raise BcnRequestError(
                DeliveryFailure.UNKNOWN,
                "timeout",
                f"BCN switch_bot timeout: {e}",
            )
        except BcnServiceError:
            raise
        except Exception as e:
            logger.error(
                f"[BcnService.switch_bot] Unexpected error: {e} "
                f"provider_bot_ref={provider_bot_ref}"
            )
            raise BcnRequestError(
                DeliveryFailure.UNKNOWN,
                "unknown",
                f"BCN switch_bot error: {e}",
            )

    def delete_provider_bot(
        self,
        *,
        teamclaw_bot_uuid: str,
        owner_workno: str,
        provider_bot_ref: str | None = None,
    ) -> Dict[str, Any]:
        """逻辑删除 Provider Bot 绑定。

        调用 BCN 的 DELETE /providers/{provider_id}/bots/{provider_bot_ref} 接口。

        Args:
            provider_bot_ref: Stable service address supplied from a resolved Bot.
                Omission preserves legacy Owner-qualified personal/desktop calls.
            teamclaw_bot_uuid: TC 的 bot uuid (例如 "20260611_d5v7rui3")
            owner_workno: bot 所有者工号 (例如 "100000")

        Returns:
            BCN 删除结果摘要。dev / local 环境无 provider 凭据时返回 skipped=True。

        Raises:
            BcnServiceError: 4xx/5xx / 超时 / 其他网络异常
        """
        if provider_bot_ref is None:
            provider_bot_ref = f"{teamclaw_bot_uuid}:{owner_workno}"
        elif not provider_bot_ref.strip() or not provider_bot_ref.startswith(f"{teamclaw_bot_uuid}:"):
            raise BcnServiceError("Invalid provider_bot_ref")
        env = get_current_env()

        provider_cfg = _get_provider_config(env, self._config)
        if not provider_cfg:
            logger.info(
                f"[BcnService.delete_provider_bot] env={env} skipped "
                f"(no provider credentials), provider_bot_ref={provider_bot_ref}"
            )
            return {
                "deleted": False,
                "provider_id": "",
                "provider_bot_ref": provider_bot_ref,
                "skipped": True,
            }

        provider_id = provider_cfg["provider_id"]
        provider_admin_token = provider_cfg["provider_admin_token"]
        path = f"/providers/{provider_id}/bots/{provider_bot_ref}"
        headers = {
            "Authorization": f"Bearer {provider_admin_token}",
        }
        logger.info(
            f"[BcnService.delete_provider_bot] DELETE {path} "
            f"provider_bot_ref={provider_bot_ref}"
        )

        try:
            response = self._http.delete(path, headers=headers, timeout=self._timeout)
            response.raise_for_status()
            logger.info(
                f"[BcnService.delete_provider_bot] OK "
                f"provider_bot_ref={provider_bot_ref}"
            )
            return {
                "deleted": True,
                "provider_id": provider_id,
                "provider_bot_ref": provider_bot_ref,
            }

        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:500] if e.response else "No response"
            status = e.response.status_code if e.response else "N/A"
            logger.error(
                f"[BcnService.delete_provider_bot] HTTP error: "
                f"status={status} body={error_body} "
                f"provider_bot_ref={provider_bot_ref}"
            )
            raise bcn_http_error(
                "BCN delete_provider_bot HTTP error",
                e.response.status_code if e.response else None,
            )
        except httpx.TimeoutException as e:
            logger.error(
                f"[BcnService.delete_provider_bot] Timeout: {e} "
                f"provider_bot_ref={provider_bot_ref}"
            )
            raise BcnRequestError(
                DeliveryFailure.UNKNOWN,
                "timeout",
                f"BCN delete_provider_bot timeout: {e}",
            )
        except BcnServiceError:
            raise
        except Exception as e:
            logger.error(
                f"[BcnService.delete_provider_bot] Unexpected error: {e} "
                f"provider_bot_ref={provider_bot_ref}"
            )
            raise BcnRequestError(
                DeliveryFailure.UNKNOWN,
                "unknown",
                f"BCN delete_provider_bot error: {e}",
            )

    def sync_bot_caller_config(
        self,
        *,
        teamclaw_bot_uuid: str,
        owner_workno: str,
        enabled: bool,
        source_revision: int,
        provider_bot_ref: str | None = None,
        timeout_seconds: float | None = None,
    ) -> Dict[str, Any]:
        """同步 Bot caller 配置快照到 BCS 权威端点。

        调用 PATCH /providers/{provider_id}/bots/{provider_bot_ref}/caller,
        按 ``source_revision`` CAS(BCS 侧 per-bot 单调版本)。成功 sync_status
        ∈ {applied, unchanged};stale 表示 BCS 侧持有更高 revision(两边
        计数器分叉),按错误传播,不静默吞掉。

        只传非身份控制面数据(enabled / minimum_isolation);身份材料
        (token / session / iam)绝不进入本链路 —— bcs-domain caller_identity
        契约红线。

        Args:
            teamclaw_bot_uuid: TC 的 bot uuid
            owner_workno: bot 所有者工号
            enabled: bot 级 caller 开关(bot_call_type == CALLER)
            source_revision: ac_bots.caller_config_revision(本次 bump 后的值)
            provider_bot_ref: 已解析的稳定服务地址(Ownership handoff 后当前
                owner 不再适用于寻址);显式传入时原样作为 URL 稳定后缀,绝不重拼
                当前 owner。省略保持旧调用既有行为(Owner-qualified 默认)。
                本调用不改写 ``source_revision``。
            timeout_seconds: 单次发送的剩余超时预算（秒）；``None`` 沿用服务级
                ``self._timeout``。

        Returns:
            dict: skipped=True(env 无凭据跳过)/ sync_status + provider_bot_ref

        Raises:
            BcnServiceError: stale / 4xx / 5xx / 超时 / 其他网络异常
        """
        if provider_bot_ref is None:
            provider_bot_ref = f"{teamclaw_bot_uuid}:{owner_workno}"
        elif (
            not provider_bot_ref.strip()
            or not provider_bot_ref.startswith(f"{teamclaw_bot_uuid}:")
        ):
            raise BcnServiceError("Invalid provider_bot_ref")
        env = get_current_env()

        provider_cfg = _get_provider_config(env, self._config)
        if not provider_cfg:
            logger.info(
                f"[BcnService.sync_bot_caller_config] env={env} skipped "
                f"(no provider credentials), provider_bot_ref={provider_bot_ref}"
            )
            return {
                "skipped": True,
                "provider_bot_ref": provider_bot_ref,
            }

        provider_id = provider_cfg["provider_id"]
        provider_admin_token = provider_cfg["provider_admin_token"]
        payload: Dict[str, Any] = {
            "snapshot": {
                "source_revision": source_revision,
                "caller_identity": {
                    "enabled": enabled,
                    "minimum_isolation": "caller_container",
                },
            }
        }
        path = f"/providers/{provider_id}/bots/{provider_bot_ref}/caller"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {provider_admin_token}",
        }
        logger.info(
            f"[BcnService.sync_bot_caller_config] PATCH {path} "
            f"enabled={enabled} source_revision={source_revision}"
        )

        try:
            response = self._http.patch(
                path,
                json=payload,
                headers=headers,
                timeout=self._resolve_send_timeout(timeout_seconds),
            )
            response.raise_for_status()
            body = response.json()
            # 直达 BCS 返回裸 PATCH 响应;corp BCN 管理面包
            # {"success": bool, "data": {...}}。有信封时解开,绝不杜撰载荷。
            if isinstance(body, dict) and isinstance(body.get("success"), bool):
                if not body["success"]:
                    error_msg = (
                        body.get("error")
                        or body.get("message")
                        or "Unknown error"
                    )
                    raise BcnServiceError(
                        f"BCN caller config sync API error: {error_msg}"
                    )
                body = (
                    body.get("data") if isinstance(body.get("data"), dict) else {}
                )
            sync_status = str((body or {}).get("sync_status") or "")
            if sync_status not in ("applied", "unchanged"):
                hint = (
                    "stale = BCS holds a higher source_revision"
                    if sync_status == "stale"
                    else "unexpected response body"
                )
                message = (
                    f"BCN caller config sync rejected: sync_status={sync_status!r} "
                    f"({hint}) provider_bot_ref={provider_bot_ref} "
                    f"source_revision={source_revision}"
                )
                if sync_status == "stale":
                    # stale = 两侧 revision 计数器分叉的 CAS 冲突;调用方按
                    # kind/code 分类,不解析错误字符串。
                    raise BcnRequestError(
                        DeliveryFailure.REJECTED, "caller_conflict", message
                    )
                raise BcnServiceError(message)
            logger.info(
                f"[BcnService.sync_bot_caller_config] OK "
                f"provider_bot_ref={provider_bot_ref} sync_status={sync_status} "
                f"enabled={enabled} source_revision={source_revision}"
            )
            return {
                "skipped": False,
                "sync_status": sync_status,
                "provider_bot_ref": provider_bot_ref,
            }
        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:500] if e.response else "No response"
            status = e.response.status_code if e.response else "N/A"
            logger.error(
                f"[BcnService.sync_bot_caller_config] HTTP error: "
                f"status={status} body={error_body} "
                f"provider_bot_ref={provider_bot_ref}"
            )
            raise bcn_http_error(
                "BCN caller config sync HTTP error",
                e.response.status_code if e.response else None,
            )
        except httpx.TimeoutException as e:
            logger.error(
                f"[BcnService.sync_bot_caller_config] Timeout: {e} "
                f"provider_bot_ref={provider_bot_ref}"
            )
            raise BcnRequestError(
                DeliveryFailure.UNKNOWN,
                "timeout",
                f"BCN caller config sync timeout: {e}",
            )
        except BcnServiceError:
            raise
        except Exception as e:  # pragma: no cover - 传输层安全网
            logger.error(
                f"[BcnService.sync_bot_caller_config] Unexpected error: {e} "
                f"provider_bot_ref={provider_bot_ref}"
            )
            raise BcnRequestError(
                DeliveryFailure.UNKNOWN,
                "unknown",
                f"BCN caller config sync error: {e}",
            )

    def get_attributes(
        self, *, bot_uuid: str, timeout_seconds: float | None = None
    ) -> Dict[str, Any]:
        """读取已注册 Bot 的协作属性 (Provider 管理 API GET)。

        GET /providers/{provider_id}/bots/{bot_uuid}/attributes — 与
        register/switch/delete provider-bot 同套鉴权:仅 ``Authorization:
        Bearer {provider_admin_token}``，``provider_id`` 在 path，不传
        ``X-BCN-Provider-Id``。响应直接是属性对象 (``user_visibility`` /
        ``friend_ext`` / ``friend_check_in_strategy``)。非 prod/pre 或凭据
        空时跳过 (返 ``{"skipped": True}``)。

        Args:
            bot_uuid: 已注册的 provider bot 标识。
            timeout_seconds: 单次发送的剩余超时预算（秒）；``None`` 沿用服务级
                ``self._timeout``。

        Raises:
            BcnBotMissingError: 404 —— bot 不存在(与失败严格区分);
            BcnServiceError: 其他 4xx/5xx/超时/网络异常
                (:class:`BcnRequestError` / :class:`BcnUnavailableError`)。
        """
        env = get_current_env()
        provider_cfg = _get_provider_config(env, self._config)
        if not provider_cfg:
            return {"skipped": True}
        provider_id = provider_cfg["provider_id"]
        token = provider_cfg["provider_admin_token"]
        path = f"/providers/{provider_id}/bots/{bot_uuid}/attributes"
        try:
            response = self._http.get(
                path,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._resolve_send_timeout(timeout_seconds),
            )
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}
        except httpx.HTTPStatusError as e:
            raise bcn_http_error(
                "BCS attributes get HTTP error",
                e.response.status_code if e.response else None,
            )
        except httpx.TimeoutException as e:
            raise BcnRequestError(
                DeliveryFailure.UNKNOWN,
                "timeout",
                f"BCS attributes get timeout: {e}",
            )
        except BcnServiceError:
            raise
        except Exception as e:
            raise BcnRequestError(
                DeliveryFailure.UNKNOWN, "unknown", f"BCS attributes get error: {e}"
            )

    def patch_attributes(
        self, *, bot_uuid: str, body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """局部更新 Bot 协作属性 (Provider 管理 API PATCH)。

        PATCH /providers/{provider_id}/bots/{bot_uuid}/attributes，同套鉴权。
        body 至少含一个可更新字段 (``user_visibility`` / ``friend_ext`` /
        ``friend_check_in_strategy``)；``friend_ext`` 顶层对象整体替换、传
        ``{}`` 清空。非 prod/pre 或凭据空时跳过。
        """
        env = get_current_env()
        provider_cfg = _get_provider_config(env, self._config)
        if not provider_cfg:
            return {"skipped": True}
        provider_id = provider_cfg["provider_id"]
        token = provider_cfg["provider_admin_token"]
        path = f"/providers/{provider_id}/bots/{bot_uuid}/attributes"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            response = self._http.patch(
                path, json=body, headers=headers, timeout=self._timeout
            )
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}
        except httpx.HTTPStatusError as e:
            raise bcn_http_error(
                "BCS attributes patch HTTP error",
                e.response.status_code if e.response else None,
            )
        except httpx.TimeoutException as e:
            raise BcnRequestError(
                DeliveryFailure.UNKNOWN,
                "timeout",
                f"BCS attributes patch timeout: {e}",
            )
        except BcnServiceError:
            raise
        except Exception as e:
            raise BcnRequestError(
                DeliveryFailure.UNKNOWN,
                "unknown",
                f"BCS attributes patch error: {e}",
            )

    def check_admission(
        self,
        bot_uuid: str,
        actor: str,
        originator: str | None = None,
    ) -> Dict[str, Any]:
        """Check BCS admission: is ``actor`` allowed to access ``bot_uuid``?

        Calls ``GET /bots/{bot_uuid}/admission`` on BCS (edge-permission SoR).
        Returns the BCS ``AdmissionResult`` dict:
        ``{allowed, grants, reason_code, public_default}``.

        Used at Phase 4 cutover to replace the old ``BotFriendRepository``
        friend-check in ``SessionResourceService._resolve_upload_context``.

        Args:
            bot_uuid: BCS composite bot id (``{backend_bot_id}:{owner_workno}``).
            actor: requesting actor id (``human_<staff_no>`` or bot_uuid).
            originator: optional originator (BCS defaults to ``actor``).

        Returns:
            AdmissionResult dict from BCS.

        Raises:
            BcnServiceError: on HTTP error or timeout.
        """
        params: Dict[str, str] = {"actor": actor}
        if originator is not None:
            params["originator"] = originator

        try:
            response = self._http.get(
                f"/bots/{bot_uuid}/admission",
                params=params,
                timeout=self._timeout,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:500] if e.response else "No response"
            status = e.response.status_code if e.response else "N/A"
            logger.error(
                f"[BcnService.check_admission] HTTP error: "
                f"status={status} body={error_body} bot_uuid={bot_uuid}"
            )
            raise bcn_http_error(
                "BCS admission HTTP error",
                e.response.status_code if e.response else None,
            )
        except httpx.TimeoutException as e:
            logger.error(
                f"[BcnService.check_admission] Timeout: {e} bot_uuid={bot_uuid}"
            )
            raise BcnRequestError(
                DeliveryFailure.UNKNOWN,
                "timeout",
                f"BCS admission timeout: {e}",
            )
        except BcnServiceError:
            raise
        except Exception as e:
            logger.error(
                f"[BcnService.check_admission] Unexpected error: {e} bot_uuid={bot_uuid}"
            )
            raise BcnRequestError(
                DeliveryFailure.UNKNOWN,
                "unknown",
                f"BCS admission error: {e}",
            )


# ``BcnService`` is wired as a @singleton via the injector
# (BotManagementModule binds it; BotService receives it by ctor).
