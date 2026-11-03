"""A2A connection ownership, probing, HTTP auth and isolated WS fallback."""

from __future__ import annotations

import asyncio
from typing import Any, Literal, cast

from secbaas.community.api.bot_interaction import InteractionResolution
from secbaas.community.logger import get_logger

from ._async_chat_client_transport import EventHandler, TokenMinter
from ._async_chat_client_ws import make_ws_client
from ._async_chat_transport_base import ChatTransportBase
from ._bot_a2a_client import A2aUnavailableError, BotA2aClient
from ._chat_errors import NotConnectedError
from ._interaction_protocol import EngineInteractionResolveExchange

logger = get_logger("core-bot-run")


def _strip_ws_endpoint_suffix(path: str) -> str:
    """剥去路径尾部的 WS endpoint 后缀，保留其前的一切路由前缀。

    后缀词汇集与 _claw_service._build_ws_url / adapter.ws_path() 同源：
      - ``/api/{engine}/ws``（默认与 openclaw/claude_code adapter）
      - ``/api/ws``（aicoding adapter）
      - ``/ws``（直连形态，无前缀）
    末段不是 ``ws``（未知自定义 ws_path）则不剥——base 维持旧语义
    （host 根），由能力探测诚实回退，绝不猜前缀归属。
    """
    segments = path.strip("/").split("/") if path.strip("/") else []
    if not segments or segments[-1] != "ws":
        return ""
    if len(segments) >= 3 and segments[-3] == "api" and segments[-2] != "api":
        # /api/{engine}/ws 与 /api/ws（aicoding 的 engine 段就是 "ws"）
        segments = segments[:-3]
    elif len(segments) >= 2 and segments[-2] == "api":
        segments = segments[:-2]
    else:
        segments = segments[:-1]
    return "/" + "/".join(segments) if segments else ""


def _a2a_base_url_from_ws(uri: str) -> str | None:
    """ws(s)://host:port{prefix}/ws-endpoint → http(s)://host:port{prefix}。

    A2A 端点与 v3 WS 端点同进程同端口（engine adapter :20003 单实例双面）；
    真实调用方 _build_ws_url 产出 ``wss://proxy/proxypass/<target>{ws_path}``
    形状——代理路由前缀必须保留在 base 上（只剥 WS endpoint 后缀，从后向前
    按词汇集剥），否则 card/RPC 打到代理根，grey 永远回退、on 连不上。
    """
    if not isinstance(uri, str) or "://" not in uri:
        return None
    scheme, _, rest = uri.partition("://")
    if scheme not in ("ws", "wss"):
        return None
    host_port, _, raw_path = rest.partition("/")
    prefix = _strip_ws_endpoint_suffix("/" + raw_path) if raw_path else ""
    return ("https://" if scheme == "wss" else "http://") + host_port + prefix


class A2aChatTransport(ChatTransportBase):
    """Own all A2A transport state without access to the conversation facade."""

    def __init__(
        self,
        uri: str,
        mode: str,
        *,
        headers: dict[str, str] | None = None,
        client_id: str | None = None,
        client_version: str = "1.0.0",
        ignore_case: bool = False,
        proxypass_token_minter: TokenMinter | None = None,
    ) -> None:
        super().__init__(
            uri, headers=headers, client_id=client_id, client_version=client_version
        )
        if mode not in {"grey", "on"}:
            raise ValueError(f"invalid A2A transport mode: {mode!r}")
        self.mode = mode
        self._ignore_case = ignore_case
        self._proxypass_token_minter = proxypass_token_minter
        self._current_name = "unknown"
        self.fallback_client: Any | None = None
        self.routed_sessions: dict[str, str] = {}
        self._fallback_lock = asyncio.Lock()

    @property
    def transport_name(self) -> str:
        return self._current_name if self._client is not None else "unknown"

    def refresh_outbound_auth(self, headers: dict[str, str] | None) -> None:
        super().refresh_outbound_auth(headers)
        if self._current_name == "a2a" and self._client is not None:
            self._client.refresh_proxypass_token(headers)

    def _make_ws_client(self) -> Any:
        return make_ws_client(
            uri=self.uri,
            client_id=self.client_id,
            client_version=self.client_version,
            headers=self.headers,
        )

    async def connect(self) -> dict[str, Any]:
        if self._client is not None:
            raise RuntimeError("Already connected")
        a2a_base = _a2a_base_url_from_ws(self.uri)
        if self.mode == "on" and a2a_base is None:
            raise A2aUnavailableError(
                "no HTTP base could be derived from the ws endpoint"
            )
        if a2a_base is not None:
            try:
                candidate = BotA2aClient(base_url=a2a_base, headers=self.headers)
                await self._connect_client(candidate)
                self._current_name = "a2a"
                return dict(candidate.server_info or {})
            except A2aUnavailableError as exc:
                if self.mode == "on":
                    raise
                logger.info("[a2a-transport] probing failed (%s) — v3", exc)

        hello = await self._connect_client(self._make_ws_client())
        self._current_name = "v3-ws"
        return hello

    async def _request_client(
        self,
        *,
        session_key: str,
        attachments: list[Any] | None,
        request_kind: Literal["send", "inject"],
    ) -> Any:
        if self._current_name == "a2a" and attachments:
            target = await self._ensure_fallback()
            if request_kind == "send":
                self.routed_sessions[session_key] = "v3"
            return target
        if request_kind == "send":
            self.routed_sessions.pop(session_key, None)
        return self._require_client()

    def _abort_client(self, session_key: str) -> Any:
        # A main reconnect may select WS while older fallback runs still exist.
        if session_key in self.routed_sessions and self.fallback_client is not None:
            return self.fallback_client
        return self._require_client()

    def can_resolve_interaction(self, session_key: str) -> bool:
        if session_key in self.routed_sessions:
            return self.fallback_client is not None and bool(
                self.fallback_client.connected
            )
        return super().can_resolve_interaction(session_key)

    async def resolve_interaction(
        self,
        *,
        session_key: str,
        interaction_id: str,
        resolution: InteractionResolution,
    ) -> EngineInteractionResolveExchange:
        if session_key in self.routed_sessions:
            if self.fallback_client is None:
                raise NotConnectedError("engine websocket is disconnected")
            return cast(
                "EngineInteractionResolveExchange",
                await self.fallback_client.interaction_resolve(
                    interaction_id=interaction_id, resolution=resolution
                ),
            )
        if self._current_name != "a2a":
            return await super().resolve_interaction(
                session_key=session_key,
                interaction_id=interaction_id,
                resolution=resolution,
            )
        client = self._require_client()
        # Background HITL bypasses pool.get(). Only the actual HTTP leg mints;
        # WS main/fallback must never mint or receive A2A resolve arguments.
        if self._proxypass_token_minter is not None:
            try:
                self.refresh_outbound_auth(
                    {"x-proxypass-token": await self._proxypass_token_minter()}
                )
            except Exception:
                logger.warning(
                    "[a2a-transport] proxypass re-mint failed; "
                    "resolve continues on last-known token",
                    exc_info=True,
                )
        return cast(
            "EngineInteractionResolveExchange",
            await client.interaction_resolve(
                interaction_id=interaction_id,
                resolution=resolution,
                session_key=session_key,
            ),
        )

    async def _ensure_fallback(self) -> Any:
        async with self._fallback_lock:
            if self.fallback_client is not None:
                if getattr(self.fallback_client, "connected", False):
                    return self.fallback_client
                self._fallback_disconnected(self.fallback_client)

            client = self._make_ws_client()
            callbacks = self._event_callbacks()
            for event, handler in callbacks.events.items():
                client.on_event(event, self._fallback_bridge(client, event, handler))
            client.on_disconnect(
                lambda _event, _payload: self._fallback_disconnected(client)
            )
            self.fallback_client = client
            try:
                await client.connect()
            except BaseException:
                self.fallback_client = None
                try:
                    await client.close()
                except Exception:
                    logger.exception(
                        "Failed to dispose of fallback handshake candidate"
                    )
                raise
            return client

    def _fallback_bridge(
        self, client: Any, event: str, handler: EventHandler
    ) -> EventHandler:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if self.fallback_client is not client:
                return None
            # Process ownership at its source, even when the facade has no waiter.
            # Main events, wildcard logs and inject terminals cannot release it.
            if event in {"chat", "agent", "error"} and args:
                payload = args[-1]
                if isinstance(payload, dict):
                    self._release_terminal_ownership(payload)
            return handler(*args, **kwargs)

        return wrapped

    def _fallback_disconnected(self, client: Any) -> None:
        if self.fallback_client is not client:
            return
        routed = tuple(self.routed_sessions)
        self.routed_sessions.clear()
        self.fallback_client = None
        logger.warning(
            "[a2a-fallback] disconnected: terminating %d routed session(s)", len(routed)
        )
        self._event_callbacks().on_sessions_failed(
            routed,
            "fallback connection lost; run outcome unknown",
            "fallback_disconnect",
        )

    def has_active_sessions(self) -> bool:
        return super().has_active_sessions() or bool(self.routed_sessions)

    def tracks_session(self, session_key: str) -> bool:
        return session_key in self.routed_sessions or super().tracks_session(
            session_key
        )

    def _fuzzy_routed_key(self, session_key: str) -> str | None:
        if session_key in self.routed_sessions:
            return session_key
        target = session_key.lower() if self._ignore_case else session_key
        for stored_key in self.routed_sessions:
            candidate = stored_key.lower() if self._ignore_case else stored_key
            if candidate in target:
                return stored_key
        return None

    def _release_terminal_ownership(self, payload: dict[str, Any]) -> None:
        if payload.get("state") not in {"final", "error"}:
            return
        run_id = payload.get("runId") or payload.get("run_id")
        if isinstance(run_id, str) and run_id.startswith("inject-"):
            return
        if payload.get("stopReason") == "inject":
            return
        session_key = payload.get("sessionKey", "")
        if not isinstance(session_key, str) or not session_key:
            return
        reg_key = self._fuzzy_routed_key(session_key)
        if reg_key is not None:
            self.routed_sessions.pop(reg_key, None)

    def _send_rejected(self, session_key: str) -> None:
        self.routed_sessions.pop(session_key, None)

    async def close(self) -> None:
        try:
            await super().close()
        finally:
            client, self.fallback_client = self.fallback_client, None
            self.routed_sessions.clear()
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    logger.exception("A2A fallback close failed")
