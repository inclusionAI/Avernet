"""Aiohttp-based BotService plugin — HTTP implementation.

Implements fire-and-forget POST /api/bot-chat/log-relations via aiohttp.
When base_url is empty the plugin is effectively a no-op (report returns
immediately). Failures are logged as WARNING and never propagated.

Also implements GET /api/service-bot/publish/{bot_id}/binding for bot
binding lookups. Unlike report(), get_binding() propagates errors via
PaasError because callers need the data or a clear failure signal.

Reference: RFC 0002 atomic commit 1
"""

from __future__ import annotations

import time
from typing import Any

import aiohttp

from secbaas.community.api.device_manage import ErrorCode, PaasError
from secbaas.community.logger import get_logger
from secbaas.community.spi.bot_service import (
    BotBindingData,
    BotServicePlugin,
    LogRelationPayload,
)

logger = get_logger("plugin-bot-service")


class AiohttpBotServicePlugin(BotServicePlugin):
    """Aiohttp-based BotService plugin — production HTTP implementation.

    Encapsulates POST /api/bot-chat/log-relations with fire-and-forget
    semantics:

      - ``base_url`` empty → ``report()`` returns immediately (noop)
      - HTTP errors logged as WARNING, never raised
      - No retry logic (fire-and-forget)

    And GET /api/service-bot/publish/{bot_id}/binding with error-propagation
    semantics:

      - ``base_url`` empty → ``get_binding()`` raises PaasError(CONFIG_INVALID)
      - HTTP errors, transport errors, envelope failures → raise PaasError

    Usage::

        plugin = AiohttpBotServicePlugin(
            base_url="https://log-relations.example.com",
            timeout=10.0,
        )
        payload = LogRelationPayload(...)
        await plugin.report(payload)
        binding = await plugin.get_binding("bot_001", "owner_001", "online")
        await plugin.close()
    """

    def __init__(self, base_url: str = "", timeout: float = 10.0) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp.ClientSession."""
        if self._session is not None and not self._session.closed:
            return self._session
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout),
        )
        self._session = session
        return session

    # ───────────────────────── report (fire-and-forget) ─────────────────────────

    async def report(self, payload: LogRelationPayload) -> None:
        """Send a log-relation POST request (fire-and-forget).

        - base_url empty → return immediately, no request sent
        - failures logged as WARNING, never raised

        Args:
            payload: Log-relation request body.
        """
        if not self._base_url:
            return

        url = f"{self._base_url.rstrip('/')}/api/bot-chat/log-relations"

        try:
            session = await self._get_session()
            async with session.post(url, json=payload.to_dict()) as resp:
                body = await resp.text()
                if resp.status >= 400:
                    logger.warning(
                        "[bot-service] log-relation POST failed: "
                        "status=%d body=%s biz_task_id=%s",
                        resp.status,
                        body,
                        payload.biz_task_id,
                    )
                else:
                    logger.debug(
                        "[bot-service] log-relation POST ok: status=%d biz_task_id=%s",
                        resp.status,
                        payload.biz_task_id,
                    )
        except (TimeoutError, aiohttp.ClientError) as e:
            logger.warning(
                "[bot-service] log-relation POST error: biz_task_id=%s error=%s",
                payload.biz_task_id,
                e,
            )

    # ───────────────────────── get_binding (error-propagating) ──────────────────

    async def _raise_for_http_error(
        self,
        response: aiohttp.ClientResponse,
    ) -> None:
        """Map HTTP status to PaasError. Raises on 4xx / 5xx."""
        if response.status < 400:
            return
        try:
            body: dict[str, Any] = await response.json()
            message = body.get("message", body.get("error", ""))
        except Exception:
            message = await response.text()

        if response.status in (401, 403):
            raise PaasError(
                ErrorCode.AUTH_FAILED,
                f"AgentClaw auth failed (HTTP {response.status}): {message}",
            )
        if response.status == 429:
            raise PaasError(
                ErrorCode.RATE_LIMITED,
                f"AgentClaw rate limited: {message}",
            )
        if 400 <= response.status < 500:
            raise PaasError(
                ErrorCode.CONFIG_INVALID,
                f"AgentClaw client error (HTTP {response.status}): {message}",
            )
        if response.status >= 500:
            raise PaasError(
                ErrorCode.PLATFORM_ERROR,
                f"AgentClaw server error (HTTP {response.status}): {message}",
            )

    @staticmethod
    def _check_response_envelope(data: dict[str, Any]) -> dict[str, Any]:
        """Validate the GET binding response envelope.

        Expected: ``{"success": bool, "message": str, "error_code": int|null,
        "data": {...}}``

        Returns the inner ``data`` dict on success.
        Raises PaasError when success is falsy or data is missing/invalid.
        """
        if not data.get("success", False):
            error_code_raw = data.get("error_code")
            message = data.get("message", "API returned failure without message")
            is_not_found = (
                error_code_raw == 404
                or (
                    error_code_raw == 500
                    and "no success publish found".lower() in message.lower()
                )
                or (
                    error_code_raw == 500
                    and "no validating publish found".lower() in message.lower()
                )
            )
            if is_not_found:
                raise PaasError(
                    ErrorCode.NOT_FOUND,
                    f"AgentClaw binding not found (code={error_code_raw}): {message}",
                )
            raise PaasError(
                ErrorCode.PLATFORM_ERROR,
                f"AgentClaw binding API error (code={error_code_raw}): {message}",
            )

        inner = data.get("data")
        if inner is None:
            raise PaasError(
                ErrorCode.PLATFORM_ERROR,
                "AgentClaw binding API response missing data payload",
            )
        if not isinstance(inner, dict):
            raise PaasError(
                ErrorCode.PLATFORM_ERROR,
                f"AgentClaw binding API response data is not a dict "
                f"(got {type(inner).__name__})",
            )
        return inner

    async def _get_binding_raw(
        self,
        bot_id: str,
        owner_id: str,
        stage: str,
    ) -> dict[str, Any]:
        """GET /api/service-bot/publish/{bot_id}/binding with error propagation.

        Returns validated inner ``data`` dict.
        Raises PaasError on transport failure, HTTP error, or envelope failure.
        """
        url = f"{self._base_url.rstrip('/')}/api/service-bot/publish/{bot_id}/binding"
        params = {"owner_id": owner_id, "stage": stage}

        try:
            session = await self._get_session()
            async with session.get(url, params=params) as resp:
                await self._raise_for_http_error(resp)
                data: dict[str, Any] = await resp.json()
                logger.debug(
                    "[bot-service] get_binding url=%s params=%s status=%d",
                    url,
                    params,
                    resp.status,
                )
            return self._check_response_envelope(data)
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise PaasError(
                ErrorCode.PLATFORM_UNAVAILABLE,
                f"AgentClaw binding request failed: {exc}",
            ) from exc

    async def get_binding(
        self,
        bot_id: str,
        owner_id: str,
        stage: str,
    ) -> BotBindingData:
        """Query bot binding info via GET /api/service-bot/publish/{bot_id}/binding.

        When stage == "all", queries in order online → verify → draft and
        returns the first successful result.

        Args:
            bot_id: Bot identifier.
            owner_id: Owner entity identifier.
            stage: Lifecycle stage (online, verify, draft, or all).

        Returns:
            BotBindingData with binding details.

        Raises:
            PaasError: On empty base_url, transport error, HTTP error, or
                       envelope failure.
        """
        if not self._base_url:
            raise PaasError(
                ErrorCode.CONFIG_INVALID,
                "AgentClaw base_url is not configured; cannot query binding",
            )

        stages = ["online", "verify", "draft"] if stage == "all" else [stage]

        _binding_t0 = time.monotonic()
        last_error: PaasError | None = None
        for s in stages:
            _stage_t0 = time.monotonic()
            try:
                inner = await self._get_binding_raw(bot_id, owner_id, s)
            except PaasError as e:
                _stage_ms = (time.monotonic() - _stage_t0) * 1000
                last_error = e
                if e.code == ErrorCode.NOT_FOUND:
                    logger.warning(
                        "[bot-service] get_binding not found: "
                        "bot_id=%s stage=%s elapsed=%.0fms error=%s",
                        bot_id,
                        s,
                        _stage_ms,
                        e,
                    )
                    continue
                raise
            _stage_ms = (time.monotonic() - _stage_t0) * 1000
            logger.info(
                "[bot-service] get_binding hit: bot_id=%s stage=%s "
                "elapsed=%.0fms total=%.0fms",
                bot_id,
                s,
                _stage_ms,
                (time.monotonic() - _binding_t0) * 1000,
            )
            logger.info(
                "[bot-service] get_binding raw: bot_id=%s engine_type=%r "
                "template_type=%r template_runtime_engine_type=%r device_provider=%r",
                inner.get("bot_id", bot_id),
                inner.get("engine_type"),
                inner.get("template_type"),
                inner.get("template_runtime_engine_type"),
                inner.get("device_provider"),
            )
            return BotBindingData(
                bot_id=inner.get("bot_id", bot_id),
                owner_id=inner.get("owner_id", owner_id),
                bot_type=inner.get("bot_type", ""),
                engine_type=inner.get("engine_type", "openclaw"),
                publish_id=inner.get("publish_id"),
                publish_status=inner.get("publish_status"),
                binding_id=inner.get("binding_id", 0),
                device_provider=inner.get("device_provider", ""),
                device_id=inner.get("device_id", ""),
                template_type=inner.get("template_type"),
                template_runtime_engine_type=inner.get("template_runtime_engine_type"),
            )

        if last_error is not None and last_error.code != ErrorCode.PLATFORM_UNAVAILABLE:
            raise last_error
        raise PaasError(
            ErrorCode.NOT_FOUND,
            f"AgentClaw binding not found for any stage: bot_id={bot_id}, "
            f"stage={stage}",
        )

    # ───────────────────────── 生命周期 ─────────────────────────

    async def close(self) -> None:
        """Close the underlying aiohttp.ClientSession."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> AiohttpBotServicePlugin:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> bool:
        await self.close()
        return False
