"""Aiohttp-based BotService plugin — HTTP implementation.

Implements fire-and-forget POST /api/bot-chat/log-relations via aiohttp.
When base_url is empty the plugin is effectively a no-op (report returns
immediately). Failures are logged as WARNING and never propagated.

Also implements GET /api/service-bot/publish/{bot_id}/binding for bot
binding lookups. Unlike report(), get_binding() propagates errors via
PaasError because callers need the data or a clear failure signal.

Also implements POST /api/v1/expert-chats/app-caller-connection for
caller-mode sandbox provisioning: the Principal JWT is minted per request
by the injected ``CallerPrincipalSigner`` (shared signing key via the
secret store) and ``need_poll`` responses are re-queried with bounded
backoff.

Once the instance is ready, GET /api/v1/token/iam is called once with the
caller's cookie to refresh IAM or DingTalk Caller execution credentials.

Reference: RFC 0002 atomic commit 1
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Sequence
from typing import Any, Literal

import aiohttp
from pydantic import ValidationError

from secbaas.community.api.device_manage import (
    ErrorCode,
    HeaderOperationRule,
    OutBoundOperationRule,
    OutBoundOperationRuleUpdatedMode,
    PaasError,
)
from secbaas.community.api.device_manage import (
    PaasServiceFacade as PaasServiceFacadeProtocol,
)
from secbaas.community.http_header import get_http_header_plugin
from secbaas.community.logger import get_logger
from secbaas.community.spi.bot_service import (
    BotBindingData,
    BotServicePlugin,
    CallerConnectionData,
    CallerExecutionCredential,
    CallerInstanceStatus,
    IamTokenData,
    LogRelationPayload,
    Result,
)

from ._principal_signer import CallerPrincipalSigner

logger = get_logger("plugin-bot-service")

_SUPPORTED_RUNTIME_ENGINE_TYPES = frozenset(
    {
        "openclaw",
        "teclaw",
        "aicoding",
        "hermes",
        "claude_code",
        "deepseek_harness",
    }
)
_CLAUDE_CODE_NORMAL_TEMPLATE = "normalCC"
_BINDING_MAX_ATTEMPTS = 2
_BINDING_RETRY_DELAY_SECONDS = 0.1
_CALLER_POLL_INITIAL_DELAY_SECONDS = 1.0
_CALLER_POLL_BACKOFF_FACTOR = 1.5
_CALLER_POLL_MAX_DELAY_SECONDS = 5.0
_CALLER_POLL_DEADLINE_SECONDS = 1800.0


def resolve_claude_code_engine(active_engine: str, template_type: str | None) -> str:
    """Resolve the adapter engine for a ``claude_code`` active engine.

    A ``claude_code`` active engine routes to the ``aicoding`` adapter while the
    bot still carries a non-normal template, and to ``claude_code`` once the bot
    uses the plain (``normalCC``) template. All other engines are unchanged.
    """
    if active_engine != "claude_code":
        return active_engine
    tt = template_type.strip() if isinstance(template_type, str) else ""
    if tt and tt != _CLAUDE_CODE_NORMAL_TEMPLATE:
        return "aicoding"
    return "claude_code"


def _validation_error_to_paas(e: ValidationError) -> PaasError:
    """Map an envelope-validation failure to PaasError.

    只透出字段路径（loc）不透出字段值——避免把 Principal / 连接 token 等
    敏感内容带进异常消息和日志。
    """
    errors = e.errors()
    first_loc = errors[0]["loc"] if errors else ()
    return PaasError(
        ErrorCode.PLATFORM_ERROR,
        f"AgentClaw response does not match the envelope contract "
        f"({len(errors)} error(s), first at {first_loc})",
    )


async def _raise_for_http_error(
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
            f"auth failed (HTTP {response.status}): {message}",
        )
    if response.status == 429:
        raise PaasError(
            ErrorCode.RATE_LIMITED,
            f"rate limited: {message}",
        )
    if 400 <= response.status < 500:
        raise PaasError(
            ErrorCode.CONFIG_INVALID,
            f"client error (HTTP {response.status}): {message}",
        )
    if response.status >= 500:
        raise PaasError(
            ErrorCode.PLATFORM_ERROR,
            f"server error (HTTP {response.status}): {message}",
        )


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

    def __init__(
        self,
        base_url: str = "",
        timeout: float = 10.0,
        principal_signer: CallerPrincipalSigner | None = None,
        paas_service_facade: PaasServiceFacadeProtocol | None = None,
        caller_token_domains: Sequence[str] = (),
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._principal_signer = principal_signer
        self._paas_service_facade = paas_service_facade
        self._caller_token_domains = tuple(caller_token_domains)
        self._session: aiohttp.ClientSession | None = None

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

    @staticmethod
    def _check_binding_envelope(result: Result[BotBindingData]) -> BotBindingData:
        """Validate the GET binding response envelope (typed).

        Business failure heuristics: error_code 404, or 500 with a
        "no (success|validating) publish found" message, map to NOT_FOUND;
        anything else to PLATFORM_ERROR.

        Returns the typed ``data`` on success.
        """
        if not result.success:
            error_code = result.error_code
            message = result.message or "API returned failure without message"
            is_not_found = (
                error_code == 404
                or (
                    error_code == 500
                    and "no success publish found".lower() in message.lower()
                )
                or (
                    error_code == 500
                    and "no validating publish found".lower() in message.lower()
                )
            )
            if is_not_found:
                raise PaasError(
                    ErrorCode.NOT_FOUND,
                    f"AgentClaw binding not found (code={error_code}): {message}",
                )
            raise PaasError(
                ErrorCode.PLATFORM_ERROR,
                f"AgentClaw binding API error (code={error_code}): {message}",
            )

        if result.data is None:
            raise PaasError(
                ErrorCode.PLATFORM_ERROR,
                "AgentClaw binding API response missing data payload",
            )
        return result.data

    async def _get_binding_raw(
        self,
        bot_id: str,
        owner_id: str,
        stage: str,
        *,
        default_tag: str | None = None,
    ) -> BotBindingData:
        """GET /api/service-bot/publish/{bot_id}/binding with error propagation.

        Returns the validated typed ``data`` (BotBindingData).
        Raises PaasError on transport failure, HTTP error, or envelope failure.
        """
        url = f"{self._base_url.rstrip('/')}/api/service-bot/publish/{bot_id}/binding"
        params: dict[str, str] = {"owner_id": owner_id, "stage": stage}
        if default_tag:
            params["default_tag"] = default_tag
        headers: dict[str, str] = {}
        get_http_header_plugin().inject_header(headers)

        for attempt in range(1, _BINDING_MAX_ATTEMPTS + 1):
            try:
                session = await self._get_session()
                async with session.get(url, params=params, headers=headers) as resp:
                    await _raise_for_http_error(resp)
                    raw: Any = await resp.json()
                    logger.debug(
                        "[bot-service] get_binding url=%s params=%s status=%d",
                        url,
                        params,
                        resp.status,
                    )
                try:
                    result = Result[BotBindingData].model_validate(raw)
                except ValidationError as e:
                    raise _validation_error_to_paas(e) from e
                return self._check_binding_envelope(result)
            except BaseException as exc:
                if isinstance(exc, PaasError):
                    raise
                if attempt < _BINDING_MAX_ATTEMPTS:
                    logger.warning(
                        "[bot-service] get_binding transient request error; "
                        "retrying attempt=%d/%d bot_id=%s stage=%s error=%s",
                        attempt,
                        _BINDING_MAX_ATTEMPTS,
                        bot_id,
                        stage,
                        type(exc).__name__,
                    )
                    await asyncio.sleep(_BINDING_RETRY_DELAY_SECONDS)
                    continue
                raise PaasError(
                    ErrorCode.PLATFORM_UNAVAILABLE,
                    f"binding request failed: {exc}",
                ) from exc

        # 不可达兜底：最后一次 attempt 必然 raise；显式写出保证返回类型完备
        raise PaasError(
            ErrorCode.PLATFORM_UNAVAILABLE,
            f"binding request failed: attempts exhausted "
            f"(bot_id={bot_id}, stage={stage})",
        )

    async def get_binding(
        self, bot_id: str, owner_id: str, stage: str, *, default_tag: str | None = None
    ) -> BotBindingData:
        """Query bot binding info via GET /api/service-bot/publish/{bot_id}/binding.

        When stage == "all", queries in order online → verify → draft and
        returns the first successful result.

        Args:
            bot_id: Bot identifier.
            owner_id: Owner entity identifier.
            stage: Lifecycle stage (online, verify, draft, or all).
            default_tag: 评测环境 binding 标签，仅透传给后端按
                         default_tag 查询评测 binding（None 不追加参数）。

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
                data = await self._get_binding_raw(
                    bot_id, owner_id, s, default_tag=default_tag
                )
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
            resolved_bot_id = data.bot_id or bot_id
            bot_type = data.bot_type
            original_engine_type = data.engine_type or "openclaw"
            runtime_engine_type = data.active_runtime_engine_type
            normalized_runtime_engine_type = (
                runtime_engine_type.strip()
                if isinstance(runtime_engine_type, str)
                else ""
            )
            if normalized_runtime_engine_type in _SUPPORTED_RUNTIME_ENGINE_TYPES:
                engine_type = normalized_runtime_engine_type
            else:
                engine_type = original_engine_type
                runtime_field_present = data.active_runtime_engine_type is not None
                unsupported_nonempty_value = bool(normalized_runtime_engine_type)
                if runtime_field_present and (
                    bot_type == "personal" or unsupported_nonempty_value
                ):
                    logger.warning(
                        "[bot-service] invalid active runtime engine; fallback to "
                        "original engine: bot_id=%s bot_type=%r engine_type=%r "
                        "active_runtime_engine_type=%r fallback=%r",
                        resolved_bot_id,
                        bot_type,
                        original_engine_type,
                        runtime_engine_type,
                        engine_type,
                    )

            consumed_engine_type = resolve_claude_code_engine(
                engine_type, data.template_type
            )

            logger.info(
                "[bot-service] get_binding raw: bot_id=%s engine_type=%r "
                "active_runtime_engine_type=%r consumed_engine_type=%r "
                "template_type=%r routed_engine_type=%r device_provider=%r",
                resolved_bot_id,
                original_engine_type,
                runtime_engine_type,
                engine_type,
                data.template_type,
                consumed_engine_type,
                data.device_provider,
            )
            return BotBindingData(
                bot_id=resolved_bot_id,
                owner_id=data.owner_id or owner_id,
                bot_type=bot_type,
                engine_type=consumed_engine_type,
                publish_id=data.publish_id,
                publish_status=data.publish_status,
                binding_id=data.binding_id,
                device_provider=data.device_provider,
                device_id=data.device_id,
                template_type=data.template_type,
            )

        if last_error is not None and last_error.code != ErrorCode.PLATFORM_UNAVAILABLE:
            raise last_error
        raise PaasError(
            ErrorCode.NOT_FOUND,
            f"AgentClaw binding not found for any stage: bot_id={bot_id}, "
            f"stage={stage}",
        )

    # ─────────────────── get_caller_connection (error-propagating) ───────────────────

    def _check_caller_connection_config(self) -> CallerPrincipalSigner:
        """caller-connection 系列入口共用的部署配置校验（fail fast）。

        返回就绪的 principal 签发器，供轮询循环每次请求现签。
        """
        if not self._base_url:
            raise PaasError(
                ErrorCode.CONFIG_INVALID,
                "caller-connection requires a non-empty bot_service base_url",
            )
        if self._principal_signer is None:
            raise PaasError(
                ErrorCode.CONFIG_INVALID,
                "caller-connection requires a principal signer "
                "(shared signing key + app identity)",
            )
        return self._principal_signer

    async def get_caller_connection(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        credential: CallerExecutionCredential,
    ) -> str:
        """POST /api/v1/expert-chats/app-caller-connection：拉起并安装调用方凭据。

        轮询/重试语义见 :meth:`_poll_caller_connection_ready`。实例就绪后按凭据
        类型单次安装 Caller 运行时身份（:meth:`_install_caller_runtime_identity`，
        失败不重试）：

        - ``iam-token``: GET /api/v1/token/iam?token_type=iam with the IAM-token cookie;
        - ``dingtalk``: GET /api/v1/token/iam with ``token_type=dingtalk``;
        - ``agentpass``: append an outbound ``x-caller-token`` rule
          to the ready sandbox through the BaaS PaasServiceFacade. The operator
          must provide the immutable domain allowlist in deployment config;
          request data cannot enlarge it.

        Args:
            bot_id: Bot identifier (bare, without the entity suffix).
            owner_id: Owner entity identifier.
            user_id: Caller user identifier.
            credential: Opaque caller execution credential. Its value is never
                logged or persisted.

        Returns:
            The ready instance's sandbox_id (``connection.target``).

        Raises:
            PaasError: On transport failure, HTTP error, envelope failure,
                the instance not becoming ready within the poll deadline, or
                the credential install failing.
        """
        self._check_caller_connection_config()
        if not credential.token:
            raise PaasError(
                ErrorCode.CONFIG_INVALID,
                "caller-connection requires a non-empty execution credential",
            )
        if not (
            credential.is_iam_token or credential.is_agentpass or credential.is_dingtalk
        ):
            raise PaasError(
                ErrorCode.CONFIG_INVALID,
                "caller-connection received an unsupported credential "
                f"type: {credential.type}",
            )
        if credential.is_agentpass and not self._caller_token_domains:
            raise PaasError(
                ErrorCode.CONFIG_INVALID,
                "AgentPass caller credential requires configured caller domains",
            )
        sandbox_id = await self._poll_caller_connection_ready(
            bot_id=bot_id, owner_id=owner_id, user_id=user_id
        )
        # 沙箱已就绪：按凭据类型单次安装 Caller 运行时身份，失败不重试。
        await self._install_caller_runtime_identity(
            bot_id=bot_id,
            credential=credential,
            sandbox_id=sandbox_id,
        )
        logger.info(
            "[bot-service] caller-connection ready: bot_id=%s owner_id=%s "
            "user_id=%s credential_type=%s sandbox_id=%s",
            bot_id,
            owner_id,
            user_id,
            credential.type,
            sandbox_id,
        )
        return sandbox_id

    async def get_existing_caller_connection(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
    ) -> str:
        """复用已就绪的 caller 实例：只拉起，不安装任何运行时凭据。

        供 resolver 的 user_id-only 分支调用——调用前已通过
        app-caller-instance/status 确认该用户实例已登记且存活，实例的执行
        凭据在先前带 token 创建时已安装，这里只取 connection.target。
        轮询/重试语义见 :meth:`_poll_caller_connection_ready`。

        Args:
            bot_id: Bot identifier (bare, without the entity suffix).
            owner_id: Owner entity identifier.
            user_id: Caller user identifier.

        Returns:
            The ready instance's sandbox_id (``connection.target``).

        Raises:
            PaasError: On transport failure, HTTP error, envelope failure, or
                the instance not becoming ready within the poll deadline.
        """
        self._check_caller_connection_config()
        sandbox_id = await self._poll_caller_connection_ready(
            bot_id=bot_id, owner_id=owner_id, user_id=user_id
        )
        logger.info(
            "[bot-service] caller-connection ready: bot_id=%s owner_id=%s "
            "user_id=%s credential_type=none sandbox_id=%s",
            bot_id,
            owner_id,
            user_id,
            sandbox_id,
        )
        return sandbox_id

    async def _poll_caller_connection_ready(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
    ) -> str:
        """POST /api/v1/expert-chats/app-caller-connection 轮询直到实例就绪。

        All parameters go in the URL query; a short-TTL app-principal JWT is
        minted by the injected ``CallerPrincipalSigner`` for every attempt and
        placed in ``X-Avernet-Principal``; ``force_upgrade`` stays ``false`` on
        every request so polling never re-triggers the upgrade path.
        ``need_poll`` responses are re-queried with an increasing delay until
        ``_CALLER_POLL_DEADLINE_SECONDS``. Transport failures get the same
        bounded re-query (a timed-out request may still have triggered the
        upgrade server-side); PaasError from HTTP status or envelope checks
        still fails fast.

        Returns:
            The ready instance's sandbox_id (``connection.target``).
        """
        signer = self._check_caller_connection_config()
        url = f"{self._base_url.rstrip('/')}/api/v1/expert-chats/app-caller-connection"
        params = {
            "bot_id": bot_id,
            "owner_id": owner_id,
            "user_id": user_id,
            "force_upgrade": "false",
        }
        logger.info("[send caller-connection], url: %s, params: %s", url, params)
        deadline = time.monotonic() + _CALLER_POLL_DEADLINE_SECONDS
        delay = _CALLER_POLL_INITIAL_DELAY_SECONDS
        last_transport_error: Exception | None = None
        raw: Any = None

        while True:
            request_id = str(uuid.uuid4())
            principal = signer.mint()
            headers = {
                "Accept": "application/json",
                "X-Avernet-Principal": principal,
                "X-Request-ID": request_id,
            }
            get_http_header_plugin().inject_header(headers)
            try:
                session = await self._get_session()
                async with session.post(url, params=params, headers=headers) as resp:
                    await _raise_for_http_error(resp)
                    raw = await resp.json()
                    logger.info(
                        "[bot-service] caller-connection request_id=%s status=%d "
                        "response_type=%s",
                        request_id,
                        resp.status,
                        type(raw).__name__,
                    )
            except (TimeoutError, aiohttp.ClientError, ValueError) as e:
                last_transport_error = e
                logger.warning(
                    "[bot-service] caller-connection request error; will "
                    "re-query: request_id=%s bot_id=%s owner_id=%s user_id=%s "
                    "error=%s",
                    request_id,
                    bot_id,
                    owner_id,
                    user_id,
                    e,
                )
            else:
                last_transport_error = None
                try:
                    result = Result[CallerConnectionData].model_validate(raw)
                except ValidationError as e:
                    # 200 但形状不符 = 契约破坏，快速失败（不进入轮询）
                    raise _validation_error_to_paas(e) from e
                caller_data = self._check_caller_envelope(result)
                if not caller_data.need_poll:
                    return self._extract_caller_sandbox_id(caller_data, request_id)
                instance_status = (
                    caller_data.instance.status if caller_data.instance else None
                )
                logger.info(
                    "[bot-service] caller-connection need_poll: request_id=%s "
                    "bot_id=%s owner_id=%s user_id=%s instance_status=%s",
                    request_id,
                    bot_id,
                    owner_id,
                    user_id,
                    instance_status,
                )

            if time.monotonic() + delay >= deadline:
                if last_transport_error is not None:
                    raise PaasError(
                        ErrorCode.PLATFORM_UNAVAILABLE,
                        f"caller-connection unreachable within "
                        f"{_CALLER_POLL_DEADLINE_SECONDS}s: bot_id={bot_id}, "
                        f"owner_id={owner_id}, user_id={user_id}",
                    ) from last_transport_error
                raise PaasError(
                    ErrorCode.DEVICE_NOT_READY,
                    f"caller-connection instance not ready within "
                    f"{_CALLER_POLL_DEADLINE_SECONDS}s: bot_id={bot_id}, "
                    f"owner_id={owner_id}, user_id={user_id}",
                )
            await asyncio.sleep(delay)
            delay = min(
                delay * _CALLER_POLL_BACKOFF_FACTOR,
                _CALLER_POLL_MAX_DELAY_SECONDS,
            )

    async def _install_caller_runtime_identity(
        self,
        *,
        bot_id: str,
        credential: CallerExecutionCredential,
        sandbox_id: str,
    ) -> None:
        """实例就绪后按凭据类型单次安装 Caller 运行时身份，失败不重试。

        - ``iam-token`` / ``dingtalk``: GET /api/v1/token/iam（Cookie 走原始
          token，``token_type`` 显式传递）；
        - ``agentpass``: 通过 BaaS outbound-rule append 安装 ``x-caller-token``。
        """
        if credential.is_iam_token:
            await self._refresh_caller_token(
                bot_id=bot_id,
                cookie=f"IAM_TOKEN={credential.token}",
                token_type="iam",
            )
        elif credential.is_dingtalk:
            await self._refresh_caller_token(
                bot_id=bot_id,
                cookie=f"IAM_TOKEN={credential.token}",
                token_type="dingtalk",
            )
        else:
            await self._append_agentpass_caller_token(
                credential=credential,
                paas_device_id=sandbox_id,
            )

    # ─────────────── get_caller_instance_status (read-only, error-propagating) ───────────────

    async def get_caller_instance_status(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
    ) -> CallerInstanceStatus:
        """GET /api/v1/expert-chats/app-caller-instance/status (self-minted Principal).

        只读观察：不创建、不恢复、不重启实例，不换签、不改账本/binding/到期时间。
        服务身份走 ``X-Avernet-Principal``（iss=``baas`` 的 BaaS 服务 JWT，与
        app-caller-connection 复用同一把共享签名密钥）。单次请求（无轮询/重试），
        超时由会话的 ``timeout`` 有界约束。

        业务信封契约（普通 ``/api/v1`` ApiResponse，非 OpenAPI Envelope）：
        ``success=false`` + ``error_code=403``（源 Bot 未找到/领域拒绝）映射
        NOT_FOUND——注意这不是 "Caller 不存在"（不存在通过 ``exists=false``
        表达）；其他业务失败（如 5999 上游探活异常）映射 PLATFORM_ERROR，
        保留 "结果未知" 语义。``data`` 缺失、``exists``/``alive`` 非严格布尔、
        或违反 ``alive → exists`` 契约不变量均按契约破坏快速失败。

        Args:
            bot_id: Bot 标识（bare，不含 entity 后缀）。
            owner_id: 容器归属实体。
            user_id: 需要查询独立 Caller 实例的用户 ID。

        Returns:
            CallerInstanceStatus(exists, alive)。

        Raises:
            PaasError: On transport failure, HTTP error, envelope failure,
                or contract violation.
        """
        if not self._base_url:
            raise PaasError(
                ErrorCode.CONFIG_INVALID,
                "caller-instance status requires a non-empty bot_service base_url",
            )
        if self._principal_signer is None:
            raise PaasError(
                ErrorCode.CONFIG_INVALID,
                "caller-instance status requires a principal signer "
                "(shared signing key + app identity)",
            )
        url = (
            f"{self._base_url.rstrip('/')}"
            "/api/v1/expert-chats/app-caller-instance/status"
        )
        params = {
            "bot_id": bot_id,
            "owner_id": owner_id,
            "user_id": user_id,
        }
        request_id = str(uuid.uuid4())
        headers = {
            "Accept": "application/json",
            "X-Avernet-Principal": self._principal_signer.mint_status(),
            "X-Request-ID": request_id,
        }
        get_http_header_plugin().inject_header(headers)
        try:
            session = await self._get_session()
            async with session.get(url, params=params, headers=headers) as resp:
                await _raise_for_http_error(resp)
                raw = await resp.json()
                logger.info(
                    "[bot-service] caller-instance status request_id=%s "
                    "status=%d response_type=%s",
                    request_id,
                    resp.status,
                    type(raw).__name__,
                )
        except (TimeoutError, aiohttp.ClientError, ValueError) as e:
            raise PaasError(
                ErrorCode.PLATFORM_UNAVAILABLE,
                "caller-instance status request failed: "
                f"bot_id={bot_id}, owner_id={owner_id}, user_id={user_id}",
            ) from e
        try:
            result = Result[CallerInstanceStatus].model_validate(raw)
        except ValidationError as e:
            # 200 但形状不符 = 契约破坏，快速失败
            raise _validation_error_to_paas(e) from e
        return self._check_status_envelope(result, request_id=request_id)

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

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp.ClientSession."""
        if self._session is not None and not self._session.closed:
            return self._session
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout),
        )
        self._session = session
        return session

    @staticmethod
    def _check_caller_envelope(
        result: Result[CallerConnectionData],
    ) -> CallerConnectionData:
        """Validate the app-caller-connection envelope (typed).

        Business error_code 403 (tenant mismatch / bot or instance missing)
        maps to NOT_FOUND; anything else to PLATFORM_ERROR.

        Returns the typed ``data`` on success.
        """
        if not result.success:
            message = result.message or "API returned failure without message"
            if result.error_code == 403:
                raise PaasError(
                    ErrorCode.NOT_FOUND,
                    f"caller-connection rejected (code=403): {message}",
                )
            raise PaasError(
                ErrorCode.PLATFORM_ERROR,
                f"caller-connection API error (code={result.error_code}): {message}",
            )

        if result.data is None:
            raise PaasError(
                ErrorCode.PLATFORM_ERROR,
                "caller-connection response missing data payload",
            )
        return result.data

    @staticmethod
    def _check_status_envelope(
        result: Result[CallerInstanceStatus],
        *,
        request_id: str,
    ) -> CallerInstanceStatus:
        """校验 app-caller-instance/status 信封（普通 ApiResponse 契约）。

        业务失败 403（源 Bot 未找到/领域拒绝）映射 NOT_FOUND；其余业务失败
        （如 5999 上游探活异常）映射 PLATFORM_ERROR——两者都不能被调用方
        误读成 ``exists=false``。``alive=true 且 exists=false`` 违反接口
        不变量，按响应合同异常处理。
        """
        if not result.success:
            message = result.message or "API returned failure without message"
            if result.error_code == 403:
                raise PaasError(
                    ErrorCode.NOT_FOUND,
                    f"caller-instance status rejected (code=403): {message}",
                )
            raise PaasError(
                ErrorCode.PLATFORM_ERROR,
                "caller-instance status API error "
                f"(code={result.error_code}): {message}",
            )
        data = result.data
        if data is None:
            raise PaasError(
                ErrorCode.PLATFORM_ERROR,
                "caller-instance status response missing data payload "
                f"(request_id={request_id})",
            )
        if data.alive and not data.exists:
            raise PaasError(
                ErrorCode.PLATFORM_ERROR,
                "caller-instance status violates contract invariant "
                f"(exists=false, alive=true) (request_id={request_id})",
            )
        return data

    async def _append_agentpass_caller_token(
        self,
        *,
        credential: CallerExecutionCredential,
        paas_device_id: str,
    ) -> None:
        """Append ``x-caller-token`` to the ready sandbox via BaaS outbound rule.

        Uses the existing update-outbound-rule path with ``mode=append`` so
        plugin/issuer rules are preserved. The raw token is only placed in the
        outbound rule payload and never copied into a log line.
        """
        assert self._paas_service_facade is not None
        rule = OutBoundOperationRule(
            header_operation_rules=[
                HeaderOperationRule(
                    domains=list(self._caller_token_domains),
                    action="set",
                    header_name="x-caller-token",
                    value=credential.token,
                )
            ]
        )
        try:
            updated = await self._paas_service_facade.update_outbound_operation_rule(
                paas_device_id=paas_device_id,
                outbound_operation_rule=rule,
                mode=OutBoundOperationRuleUpdatedMode.APPEND,
            )
        except Exception as e:
            logger.error(
                "[bot-service] AgentPass caller outbound-rule append failed: "
                "paas_device_id=%s credential_type=%s error_type=%s",
                paas_device_id,
                credential.type,
                type(e).__name__,
            )
            raise PaasError(
                ErrorCode.PLATFORM_ERROR,
                "AgentPass caller outbound-rule append failed",
            ) from e
        if not updated:
            raise PaasError(
                ErrorCode.PLATFORM_ERROR,
                "AgentPass caller outbound-rule append returned failure",
            )
        logger.info(
            "[bot-service] AgentPass caller outbound-rule appended: "
            "paas_device_id=%s credential_type=%s domains=%d",
            paas_device_id,
            credential.type,
            len(self._caller_token_domains),
        )

    @staticmethod
    def _extract_caller_sandbox_id(data: CallerConnectionData, request_id: str) -> str:
        """Extract ``connection.target`` from a ready (need_poll=false) response."""
        connection = data.connection
        if connection is None:
            raise PaasError(
                ErrorCode.PLATFORM_ERROR,
                "caller-connection response has no connection while need_poll "
                f"is false (request_id={request_id})",
            )
        # spi.bot_service 的导入在此文件被 mypy 解析为 Any（见类头 BotServicePlugin
        # 同类既有告警），此处显式标注把返回类型收紧回 str
        sandbox_id: str = connection.target
        if not sandbox_id:
            raise PaasError(
                ErrorCode.PLATFORM_ERROR,
                "caller-connection response missing connection.target "
                f"(request_id={request_id})",
            )
        return sandbox_id.removeprefix("ARCA_").removesuffix(":20003")

    async def _refresh_caller_token(
        self,
        *,
        bot_id: str,
        cookie: str,
        token_type: Literal["iam", "dingtalk"] = "iam",
    ) -> None:
        """GET /api/v1/token/iam — sandbox 就绪后单次刷新 Caller 执行凭据。

        契约：凭据交换与运行时更新是服务端副作用，响应只回显原始 IAM_TOKEN——
        回显值不消费也不入日志；该路由身份走请求 Cookie（建议完整登录态），不接受
        app Principal。带 bot_id 的请求有副作用，故单次执行、失败不重试（重试会
        重复触发交换）。凭据类型始终通过 query 参数 ``token_type`` 显式传递。
        """
        url = f"{self._base_url.rstrip('/')}/api/v1/token/iam"
        params = {
            "bot_id": bot_id,
            "is_test_exchange": "false",
            "token_type": token_type,
        }
        request_id = str(uuid.uuid4())
        headers = {
            "Accept": "application/json",
            "Cookie": cookie,
            "X-Request-ID": request_id,
        }
        try:
            session = await self._get_session()
            async with session.get(url, params=params, headers=headers) as resp:
                await _raise_for_http_error(resp)
                raw = await resp.json()
        except (TimeoutError, aiohttp.ClientError, ValueError) as e:
            raise PaasError(
                ErrorCode.PLATFORM_UNAVAILABLE,
                f"caller IAM credential refresh request failed: bot_id={bot_id}",
            ) from e
        try:
            data = IamTokenData.model_validate(raw)
        except ValidationError as e:
            # 200 但形状不符 = 契约破坏（loc-only 错误，不带 iam_token 值）
            raise _validation_error_to_paas(e) from e
        if not data.success:
            raise PaasError(
                ErrorCode.PLATFORM_ERROR,
                f"caller IAM credential refresh failed: bot_id={bot_id}, "
                f"error={data.error}",
            )
        logger.info(
            "[bot-service] caller IAM credentials refreshed: request_id=%s bot_id=%s",
            request_id,
            bot_id,
        )
