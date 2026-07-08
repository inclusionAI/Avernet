"""BCN Service - Bot Coordination Network 服务接口。

负责与 BCN (Bot Coordination Network) 服务交互，包括：
- Bot 入网注册 (onboard, 上行)
- Bot 信息同步 (name, summary)
- claude_code engine 启动时的 Provider Bot 注册 (下行, 见 register_provider_bot)
"""

from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional

import httpx

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.http_client import HttpClient
from agentclaw.community.utils.env_utils import get_current_env

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


class BcnServiceError(Exception):
    """BCN service error."""
    pass


def _get_provider_config(
    env: str,
    config: "BcnConfig",
) -> Optional[Dict[str, str]]:
    """Return the BCN provider config for ``env``, or None when there is none.

    claude_code 下行链路仅 prod / pre 两个环境注册到真实 BCN；provider_id 与
    provider_admin_token 均来自 :class:`BcnConfig`（各 corp env overlay 提供，
    社区构建默认空）。任一为空视作未配置（返回 None），``register_provider_bot``
    据此跳过（与旧 dev 路径一致）。
    """
    if env == "prod":
        provider_id = config.provider_id_prod
        provider_admin_token = config.provider_admin_token_prod
    elif env == "pre":
        provider_id = config.provider_id_pre
        provider_admin_token = config.provider_admin_token_pre
    else:
        logger.info(
            "[BcnService._get_provider_config] env=%s unsupported for "
            "provider credentials",
            env,
        )
        return None
    provider_admin_token = (provider_admin_token or "").strip()
    if not provider_id or not provider_admin_token:
        logger.info(
            "[BcnService._get_provider_config] env=%s missing provider config "
            "provider_id_present=%s provider_admin_token_present=%s",
            env,
            bool(provider_id),
            bool(provider_admin_token),
        )
        return None

    logger.info(
        "[BcnService._get_provider_config] env=%s provider config resolved "
        "provider_id=%s provider_admin_token_present=True",
        env,
        provider_id,
    )
    return {
        "provider_id": provider_id,
        "provider_admin_token": provider_admin_token,
    }


class BcnService:
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
        logger.info("[BcnService] Initialized")

    def onboard_bot(
        self,
        bot_id: str,
        name: str,
        summary: str,
        hidden: bool = False,
        request_headers: Optional[Mapping[str, str]] = None,
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

        Returns:
            BCN 返回的结果，包含：
            - bot_uuid: Bot UUID
            - onboarded: 是否已入网
            - name: Bot 名称

        Raises:
            BcnServiceError: 调用 BCN 接口失败
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

        try:
            auth_headers = _build_auth_headers(request_headers)
            response = self._http.post(
                "/admin/bots/onboard",
                json=payload,
                headers=auth_headers or None,
                timeout=self._timeout,
            )
            response.raise_for_status()

            response_data = response.json()

            # BCN 接口返回格式：{"bot_uuid": "xxx", "onboarded": true, "name": "xxx"}
            # 或者错误格式：{"error": "xxx"} 或 {"message": "xxx"}
            if "error" in response_data:
                raise BcnServiceError(
                    f"BCN API error: {response_data.get('error')}"
                )

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
            raise BcnServiceError(
                f"BCN API HTTP error: {e.response.status_code if e.response else 'N/A'} - {error_body}"
            )
        except httpx.TimeoutException as e:
            logger.error(f"[BcnService.onboard_bot] Timeout error: {e}")
            raise BcnServiceError(f"BCN API timeout: {e}")
        except Exception as e:
            logger.error(f"[BcnService.onboard_bot] Unexpected error: {e}")
            raise BcnServiceError(f"BCN API error: {e}")

    def register_provider_bot(
        self,
        *,
        teamclaw_bot_uuid: str,
        owner_workno: str,
        name: str,
        summary: str,
    ) -> Dict[str, Any]:
        """claude_code engine start 时把 bot 注册为 Provider 的 bot (下行链路)。

        语雀: doc 548864073, POST /providers/{provider_id}/bots

        Args:
            teamclaw_bot_uuid: TC 的 bot uuid (例如 "20260502_1cjjh1ik")
            owner_workno: bot 所有者工号 (例如 "100000")
            name: bot 显示名
            summary: bot 简介 (空字符串也可)

        Returns:
            BCN 返回的 dict, 关键字段:
              - bot_uuid
              - provider_id
              - provider_bot_ref (= "{teamclaw_bot_uuid}:{owner_workno}")
              - bot_runtime_token (后续下行调用凭据)
            额外字段 (本方法附加):
              - skipped: dev 环境跳过时为 True
              - idempotent_replay: 409 已注册时为 True

        Raises:
            BcnServiceError: 4xx/5xx (除 409) / 超时 / 其他网络异常
        """
        provider_bot_ref = f"{teamclaw_bot_uuid}:{owner_workno}"
        env = get_current_env()

        # dev / local 环境无 provider 凭据, 跳过实际调用 (排查日志关键字: [register_provider_bot])
        provider_cfg = _get_provider_config(env, self._config)
        if not provider_cfg:
            logger.info(
                f"[BcnService.register_provider_bot] env={env} skipped "
                f"(no provider credentials), provider_bot_ref={provider_bot_ref}"
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
        path = f"/providers/{provider_id}/bots"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {provider_admin_token}",
        }
        logger.info(
            f"[BcnService.register_provider_bot] POST {path} "
            f"provider_bot_ref={provider_bot_ref} name={name} "
            f"summary_len={len(summary or '')}"
        )

        try:
            response = self._http.post(
                path, json=payload, headers=headers, timeout=self._timeout
            )

            # 409 = 同一 (provider_id, provider_bot_ref) 已绑过, 视作幂等成功
            if response.status_code == 409:
                logger.warning(
                    f"[BcnService.register_provider_bot] 409 idempotent: "
                    f"provider_bot_ref={provider_bot_ref} already registered"
                )
                body: Dict[str, Any] = {}
                try:
                    body = response.json() or {}
                except ValueError:
                    body = {}
                body.setdefault("provider_id", provider_id)
                body.setdefault("provider_bot_ref", provider_bot_ref)
                body["idempotent_replay"] = True
                return body

            response.raise_for_status()
            response_data = response.json()
            logger.info(
                f"[BcnService.register_provider_bot] OK "
                f"bot_uuid={response_data.get('bot_uuid')} "
                f"provider_bot_ref={provider_bot_ref}"
            )
            return response_data

        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:500] if e.response else "No response"
            status = e.response.status_code if e.response else "N/A"
            logger.error(
                f"[BcnService.register_provider_bot] HTTP error: "
                f"status={status} body={error_body} "
                f"provider_bot_ref={provider_bot_ref}"
            )
            raise BcnServiceError(
                f"BCN register_provider_bot HTTP error: {status} - {error_body}"
            )
        except httpx.TimeoutException as e:
            logger.error(
                f"[BcnService.register_provider_bot] Timeout: {e} "
                f"provider_bot_ref={provider_bot_ref}"
            )
            raise BcnServiceError(f"BCN register_provider_bot timeout: {e}")
        except Exception as e:
            logger.error(
                f"[BcnService.register_provider_bot] Unexpected error: {e} "
                f"provider_bot_ref={provider_bot_ref}"
            )
            raise BcnServiceError(f"BCN register_provider_bot error: {e}")

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
                error_msg = response_data.get("error") or response_data.get("message") or "Unknown error"
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
            raise BcnServiceError(
                f"BCN switch_bot HTTP error: {status} - {error_body}"
            )
        except httpx.TimeoutException as e:
            logger.error(
                f"[BcnService.switch_bot] Timeout: {e} "
                f"provider_bot_ref={provider_bot_ref}"
            )
            raise BcnServiceError(f"BCN switch_bot timeout: {e}")
        except Exception as e:
            logger.error(
                f"[BcnService.switch_bot] Unexpected error: {e} "
                f"provider_bot_ref={provider_bot_ref}"
            )
            raise BcnServiceError(f"BCN switch_bot error: {e}")

    def delete_provider_bot(
        self,
        *,
        teamclaw_bot_uuid: str,
        owner_workno: str,
    ) -> Dict[str, Any]:
        """逻辑删除 Provider Bot 绑定。

        调用 BCN 的 DELETE /providers/{provider_id}/bots/{provider_bot_ref} 接口。

        Args:
            teamclaw_bot_uuid: TC 的 bot uuid (例如 "20260611_d5v7rui3")
            owner_workno: bot 所有者工号 (例如 "100000")

        Returns:
            BCN 删除结果摘要。dev / local 环境无 provider 凭据时返回 skipped=True。

        Raises:
            BcnServiceError: 4xx/5xx / 超时 / 其他网络异常
        """
        provider_bot_ref = f"{teamclaw_bot_uuid}:{owner_workno}"
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
            response = self._http.delete(
                path, headers=headers, timeout=self._timeout
            )
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
            raise BcnServiceError(
                f"BCN delete_provider_bot HTTP error: {status} - {error_body}"
            )
        except httpx.TimeoutException as e:
            logger.error(
                f"[BcnService.delete_provider_bot] Timeout: {e} "
                f"provider_bot_ref={provider_bot_ref}"
            )
            raise BcnServiceError(f"BCN delete_provider_bot timeout: {e}")
        except Exception as e:
            logger.error(
                f"[BcnService.delete_provider_bot] Unexpected error: {e} "
                f"provider_bot_ref={provider_bot_ref}"
            )
            raise BcnServiceError(f"BCN delete_provider_bot error: {e}")


# ``BcnService`` is wired as a @singleton via the injector
# (BotManagementModule binds it; BotService receives it by ctor).
