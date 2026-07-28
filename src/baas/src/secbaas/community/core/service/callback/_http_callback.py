# Copyright (c) 2004-2026, Ant Group.
# All Rights Reserved.

"""HTTP Callback — 实现 PostRunCallback 协议

在 Bot 执行完成后被 worker / dispatcher 调用，
根据 run_id 查库构造 CallbackPayload，通过 HTTP POST
发送到 metadata.callback_url 指定的地址。
对 5xx 响应重试一次，避免下游临时抖动导致回调丢失。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx

from secbaas.community.logger import get_logger

from ._models import CallbackPayload, CallbackResult

if TYPE_CHECKING:
    from secbaas.community.core.repository.bot_run import BotRunRepository

logger = get_logger("http-callback")

_DEFAULT_TIMEOUT: float = 10.0


class HttpCallback:
    """HTTP callback 发送实现，直接实现 PostRunCallback 协议

    调用 __call__(run_id) 时：
    1. 从 run_repository 查出 BotRunRecord
    2. 从 record.metadata 中取 callback_url
    3. 构造 CallbackPayload 并通过 HTTP POST 发送

    对 5xx 响应重试一次。
    """

    def __init__(
        self,
        run_repository: BotRunRepository,
        *,
        default_timeout: float = _DEFAULT_TIMEOUT,
        origin: str | None = None,
    ) -> None:
        self._run_repository = run_repository
        self._default_timeout = default_timeout
        self._origin = origin

    async def __call__(self, run_id: str) -> None:
        """PostRunCallback 入口：查库、构造 payload、发送。"""
        record = self._run_repository.get_by_run_id(run_id=run_id)
        if record is None:
            logger.warning("[callback] record not found: run_id=%s", run_id)
            return

        metadata = record.metadata or {}
        url: str | None = metadata.get("callback_url")
        if not url:
            logger.warning("[callback] no callback_url in metadata: run_id=%s", run_id)
            return

        payload = CallbackPayload(
            run_id=record.run_id,
            bot_id=record.bot_id,
            status=record.status,
            result=record.result_content,
            error=record.error,
            metadata=record.metadata,
        )
        result = await self._send(url, payload)
        if not result.success:
            logger.error(
                "[callback] send failed: run_id=%s, url=%s, message=%s",
                run_id,
                url,
                result.message,
            )

    async def _send(
        self,
        url: str,
        payload: CallbackPayload,
    ) -> CallbackResult:
        """发送 callback，5xx 自动重试一次。"""
        result = await self._send_once(url, payload)
        if result.status_code is not None and result.status_code >= 500:
            logger.warning(
                "[callback] retrying after %s: run_id=%s, url=%s",
                result.status_code,
                payload.run_id,
                url,
            )
            return await self._send_once(url, payload)
        return result

    async def _send_once(
        self,
        url: str,
        payload: CallbackPayload,
    ) -> CallbackResult:
        """执行一次 POST，返回 CallbackResult。"""
        body = json.dumps(payload.to_dict(), ensure_ascii=False)
        headers = {
            "Content-Type": "application/json",
        }
        if self._origin:
            headers["Origin"] = self._origin

        try:
            async with httpx.AsyncClient(timeout=self._default_timeout) as client:
                resp = await client.post(url, content=body, headers=headers)
        except httpx.HTTPError as e:
            logger.error(
                "[callback] error: run_id=%s, url=%s, %s", payload.run_id, url, e
            )
            return CallbackResult(success=False, message=str(e))

        resp_body = resp.text
        logger.info(
            "[callback] response: run_id=%s, url=%s, status=%s, body=%s",
            payload.run_id,
            url,
            resp.status_code,
            resp_body,
        )

        success = 200 <= resp.status_code < 300
        if success:
            logger.info(
                "[callback] sent: run_id=%s, url=%s, status=%s",
                payload.run_id,
                url,
                resp.status_code,
            )
        else:
            logger.error(
                "[callback] failed: run_id=%s, url=%s, status=%s",
                payload.run_id,
                url,
                resp.status_code,
            )
        return CallbackResult(
            success=success,
            status_code=resp.status_code,
            message="" if success else f"HTTP {resp.status_code}: {resp.text[:200]}",
        )
