"""BCN 上行协议客户端

实现 Provider -> BCN 的上行回调，核心接口为 POST /v1/bot/events。
当 Bot 异步执行完成后，Provider 通过此客户端向 BCN 回传 final 结果。

参考: BCN Bot 下行连接接入方案 §7
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any

import aiohttp

from secbaas.api.bcn import ChatEvent, EventResponse
from secbaas.logger import get_logger
from secbaas.spi.secret import SecretStorePlugin

logger = get_logger("core-service")

# 重试退避基数（秒），实际等待 = BASE_BACKOFF * 2^(attempt-1)
_BASE_BACKOFF = 0.5


@dataclass
class BcnUplinkConfig:
    """BCN 上行客户端配置"""

    base_url: str  # BCN 网关地址，如 https://bcn-gateway.example.com
    provider_id: str  # 本 Provider 的 ID，用于 X-BCN-Provider-Id 头
    protocol_version: str = "1.0"  # X-BCN-Protocol-Version
    timeout: float = 10.0  # HTTP 请求超时（秒）
    max_retries: int = 3  # 最大重试次数（仅 5xx / 连接失败）


class BcnUplinkClient:
    """BCN 上行协议客户端

    封装 Provider -> BCN 的 HTTP 调用，当前实现:
      - POST /v1/bot/events: 回传 Bot 最终结果

    使用示例::

        config = BcnUplinkConfig(
            base_url="https://bcn-gateway.example.com",
            provider_id="my-agent-platform",
        )
        async with BcnUplinkClient(config) as client:
            event = ChatEvent(
                run_id="r_xxx",
                message=EventMessage(text="Bot 回复内容"),
            )
            resp = await client.send_event(event, bot_id="my-bot")
    """

    def __init__(self, config: BcnUplinkConfig, secret_plugin: SecretStorePlugin):
        self._config = config
        self._secret_plugin = secret_plugin
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 aiohttp.ClientSession"""
        if self._session is not None and not self._session.closed:
            return self._session
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._config.timeout),
        )
        self._session = session
        return session

    # ───────────────────────── HTTP 头构造 ─────────────────────────

    def _build_headers(self, event_id: str, bot_id: str) -> dict[str, str]:
        """构造 BCN 上行请求通用 HTTP 头

        参考 §7.1 Provider -> BCN 通用 HTTP 头:
          - Authorization: Bearer <token>
          - Content-Type: application/json
          - X-BCN-Protocol-Version: 1.0
          - X-BCN-Timestamp: <unix-ms>
          - X-BCN-Provider-Id: <provider_id>
          - X-BCN-Event-Id: <uuid-v4>
          - X-BCN-Provider-Bot-Ref: <bot_id>
        """
        token = self._secret_plugin.get_secret("other_manual_secbaas_bcn_admin_token")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-BCN-Protocol-Version": self._config.protocol_version,
            "X-BCN-Timestamp": str(time.time_ns() // 1_000_000),
            "X-BCN-Provider-Id": self._config.provider_id,
            "X-BCN-Event-Id": event_id,
            "X-BCN-Provider-Bot-Ref": bot_id,
        }

    # ───────────────────────── 响应解析 ─────────────────────────

    @staticmethod
    def _parse_response(status: int, body: str) -> EventResponse:
        """解析 BCN 上行响应

        - 200: 成功
        - 409: 幂等重复，视为成功（deduplicated=True）
        - 4xx/5xx: 失败
        """
        ok = status == 200
        deduplicated = status == 409

        if not ok and not deduplicated:
            logger.warning(
                "[uplink] BCN responded with error: status=%d body=%s", status, body
            )

        return EventResponse(ok=ok, deduplicated=deduplicated)

    # ───────────────────────── 重试发送 ─────────────────────────

    async def _retry_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        run_id: str,
    ) -> EventResponse:
        """带指数退避的重试 HTTP 请求

        重试策略（§9.4）:
          - 连接失败 / HTTP 5xx: 重试，退避间隔 = base * 2^(attempt-1)
          - HTTP 4xx / 410 Gone: 不重试，直接返回

        Args:
            method: HTTP 方法（POST）
            url: 请求 URL
            headers: 请求头
            payload: 请求体
            run_id: 用于日志追踪

        Returns:
            EventResponse

        Raises:
            aiohttp.ClientError: 重试耗尽后仍失败
        """
        last_exc: Exception | None = None

        for attempt in range(1, self._config.max_retries + 1):
            try:
                session = await self._get_session()
                async with session.post(url, headers=headers, json=payload) as resp:
                    text = await resp.text()
                    logger.info(
                        "[uplink] POST /bot/events run_id=%s status=%d body=%s",
                        run_id,
                        resp.status,
                        text,
                    )

                    if resp.status >= 500:
                        # 5xx 服务端错误: 重试
                        logger.warning(
                            "[uplink] Server error, will retry: run_id=%s "
                            "status=%d attempt=%d/%d",
                            run_id,
                            resp.status,
                            attempt,
                            self._config.max_retries,
                        )
                        last_exc = aiohttp.ClientResponseError(
                            request_info=resp.request_info,
                            history=resp.history,
                            status=resp.status,
                            message=text,
                        )
                        # 指数退避
                        if attempt < self._config.max_retries:
                            await asyncio.sleep(_BASE_BACKOFF * (2 ** (attempt - 1)))
                        continue

                    # 4xx / 2xx: 不重试
                    return self._parse_response(resp.status, text)

            except (TimeoutError, aiohttp.ClientError) as e:
                last_exc = e
                logger.warning(
                    "[uplink] Connection error, will retry: run_id=%s "
                    "attempt=%d/%d error=%s",
                    run_id,
                    attempt,
                    self._config.max_retries,
                    e,
                )
                # 指数退避
                if attempt < self._config.max_retries:
                    await asyncio.sleep(_BASE_BACKOFF * (2 ** (attempt - 1)))
                continue

        # 重试耗尽
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(
            f"Failed to send event after {self._config.max_retries} attempts"
        )

    # ───────────────────────── 公共接口 ─────────────────────────

    async def send_event(
        self,
        event: ChatEvent,
        bot_id: str,
        event_id: str | None = None,
    ) -> EventResponse:
        """发送 Bot 结果事件到 BCN

        对应 BCN 协议 §7.2 POST /bot/events。

        Args:
            event: ChatEvent 领域模型
            bot_id: Bot 标识
            event_id: 事件 ID，用于幂等去重；不传则自动生成 UUID

        Returns:
            EventResponse: BCN 响应

        Raises:
            aiohttp.ClientError: 重试耗尽后仍失败
        """
        if event_id is None:
            event_id = str(uuid.uuid4())

        url = f"{self._config.base_url.rstrip('/')}/bot/events"
        headers = self._build_headers(event_id=event_id, bot_id=bot_id)
        payload = event.to_dict()

        logger.debug(
            "[uplink] Sending event: run_id=%s event_id=%s payload=%s",
            event.run_id,
            event_id,
            payload,
        )

        return await self._retry_request(
            "POST",
            url,
            headers=headers,
            payload=payload,
            run_id=event.run_id,
        )

    # ───────────────────────── 生命周期 ─────────────────────────

    async def close(self) -> None:
        """关闭底层 aiohttp.ClientSession"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> BcnUplinkClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> bool:
        await self.close()
        return False
