"""
ECBBotRecognitionProvider

ECB（Enterprise Context Broker）Bot 认知查询 Provider。

实现 BotCognitionProvider Protocol，从 ECB 服务获取 Bot 认知信息。

职责：
- 调用 GET /bots/{bot_id}/context 获取 Bot 上下文
- 将 ECB 结果转换为 BotCognition 领域对象
- 封装 HTTP 细节（超时、错误处理）

实现接口：
- BotCognitionProvider: src.domain.protocols.bot_cognition_protocol
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Optional

import httpx

from src.domain.protocols.bot_cognition_protocol import BotCognition

if TYPE_CHECKING:
    from src.domain.protocols.bot_cognition_protocol import BotCognitionProvider as BotCognitionProviderProtocol

logger = logging.getLogger(__name__)


class ECBBotRecognitionProvider:
    """
    ECB Bot 认知查询 Provider

    实现 BotCognitionProvider Protocol 接口。

    通过 HTTP 调用 ECB API 获取 Bot 认知信息。
    部署到 ACP 后通过 ACE 登录态自动鉴权，无需额外 token。
    """

    def __init__(self, base_url: str, timeout_ms: int = 15000) -> None:
        self._base_url = base_url
        self._timeout_ms = timeout_ms
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout_ms / 1000.0),
        )
        logger.info(f"[ECB-BotRecognition] Provider initialized, base_url={base_url}")

    def get_bot_cognition(self, bot_id: str) -> Optional[BotCognition]:
        """
        查询 Bot 认知信息

        实现 BotCognitionProvider Protocol 接口。

        Args:
            bot_id: Bot 标识

        Returns:
            BotCognition 或 None
        """
        url = f"{self._base_url}/bots/{bot_id}/context"
        result = self._request(url, f"bot_id={bot_id}")

        if result is None:
            return None

        # 转换 ECB 结果为 BotCognition
        return self._to_bot_cognition(bot_id, result)

    def _to_bot_cognition(
        self,
        bot_id: str,
        data: dict,
    ) -> Optional[BotCognition]:
        """
        将 ECB API 响应转换为 BotCognition

        Args:
            bot_id: Bot 标识
            data: ECB API 响应数据

        Returns:
            BotCognition 或 None
        """
        if not data.get("success", False):
            logger.debug(f"[ECB-BotRecognition] API returned success=false: bot_id={bot_id}")
            return None

        nodes = data.get("nodes", {})
        bots = nodes.get("bot", [])

        if not bots:
            logger.debug(f"[ECB-BotRecognition] No bot data in response: bot_id={bot_id}")
            return None

        bot_data = bots[0]

        return BotCognition(
            bot_id=bot_id,
            name=bot_data.get("name"),
            summary=bot_data.get("summary"),
            status=bot_data.get("status"),
            owner_id=bot_data.get("owner_staff_no"),
        )

    def _request(self, url: str, context_desc: str) -> Optional[dict]:
        """
        执行 HTTP 请求并返回原始响应数据

        Args:
            url: 完整请求 URL
            context_desc: 日志上下文描述

        Returns:
            响应 JSON 数据，失败返回 None
        """
        try:
            logger.debug(f"[ECB-BotRecognition] GET {url} ({context_desc})")
            resp = self._client.get(url)
            resp.raise_for_status()

            data = resp.json()
            logger.info(f"[ECB-BotRecognition] Query success, {context_desc}")
            return data

        except httpx.TimeoutException:
            logger.warning(f"[ECB-BotRecognition] Request timeout, {context_desc}")
            return None
        except httpx.HTTPStatusError as e:
            logger.warning(
                f"[ECB-BotRecognition] HTTP error, {context_desc}, "
                f"status={e.response.status_code}"
            )
            return None
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"[ECB-BotRecognition] Response parse error, {context_desc}, error={e}")
            return None
        except Exception as e:
            logger.warning(f"[ECB-BotRecognition] Unexpected error, {context_desc}, error={e}")
            return None

    def close(self) -> None:
        """关闭 HTTP 客户端"""
        self._client.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class StubBotCognitionProvider:
    """
    Bot 认知查询 Provider（Stub 实现，开发测试用）

    实现 BotCognitionProvider Protocol 接口。

    返回预设的模拟数据，不发起真实 HTTP 请求。
    """

    def __init__(
        self,
        name: str = "Stub Bot",
        summary: str = "测试用模拟Bot",
    ) -> None:
        self._name = name
        self._summary = summary

    def get_bot_cognition(self, bot_id: str) -> Optional[BotCognition]:
        return BotCognition(
            bot_id=bot_id,
            name=self._name,
            summary=self._summary,
            status="active",
            owner_id="stub_staff",
        )


# 类型检查：确保实现符合 Protocol 接口
if TYPE_CHECKING:
    _ECBBotRecognitionProvider: BotCognitionProviderProtocol = ECBBotRecognitionProvider(base_url="", timeout_ms=1000)
    _StubBotCognitionProvider: BotCognitionProviderProtocol = StubBotCognitionProvider()


__all__ = ["ECBBotRecognitionProvider", "StubBotCognitionProvider"]