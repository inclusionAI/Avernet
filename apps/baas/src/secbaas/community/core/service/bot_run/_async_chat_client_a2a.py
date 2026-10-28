"""A2A transport implementation and its A2A-only fallback state."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Literal

from secbaas.community.logger import get_logger

from ._async_chat_client_transport import AsyncChatTransportBase
from ._bot_a2a_client import A2aUnavailableError, BotA2aClient

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


class A2aChatTransport(AsyncChatTransportBase):
    """A2A transport, including grey fallback and A2A-attachment reroute.

    The object owns the fallback client, its per-run ownership table, and the
    source marker used to distinguish fallback frames from A2A frames.  The
    WebSocket implementation never sees any of this state.
    """

    def __init__(self, owner: Any, mode: str) -> None:
        super().__init__(owner)
        if mode not in {"grey", "on"}:
            raise ValueError(f"invalid A2A transport mode: {mode!r}")
        self.mode = mode
        self._current_name = "unknown"
        self.fallback_client: Any | None = None
        self.routed_sessions: dict[str, str] = {}
        self._on_fallback_frame = False
        self._fallback_lock = asyncio.Lock()

    @property
    def transport_name(self) -> str:
        return self._current_name

    async def create(self) -> tuple[Any, dict[str, Any]]:
        a2a_base = _a2a_base_url_from_ws(self.owner.uri)
        if self.mode == "on" and a2a_base is None:
            raise A2aUnavailableError(
                "no HTTP base could be derived from the ws endpoint"
            )

        if a2a_base is not None:
            try:
                candidate = BotA2aClient(
                    base_url=a2a_base,
                    headers=self.owner.headers,
                )
                await candidate.connect()
                self._current_name = "a2a"
                return candidate, dict(candidate.server_info or {})
            except A2aUnavailableError as exc:
                if self.mode == "on":
                    raise
                logger.info("[a2a-transport] probing failed (%s) — v3", exc)

        client = self.owner._make_ws_client()  # noqa: SLF001
        hello = await client.connect()
        self._current_name = "v3-ws"
        return client, hello

    async def send_target(
        self,
        *,
        session_key: str,
        attachments: list[Any] | None,
        request_kind: Literal["send", "inject"] = "send",
    ) -> Any:
        client = self.owner._client  # noqa: SLF001
        if self._current_name == "a2a" and attachments:
            target = await self._ensure_fallback()
            if request_kind == "send":
                self.routed_sessions[session_key] = "v3"
            return target
        if request_kind == "send":
            self.routed_sessions.pop(session_key, None)
        return client

    async def abort_target(self, *, session_key: str) -> Any:
        # The ownership table is authoritative for a fallback run even after a
        # grey reconnect has selected a WS main connection.
        if session_key in self.routed_sessions and self.fallback_client is not None:
            return self.fallback_client
        return self.owner._client  # noqa: SLF001

    async def interaction_target(
        self, *, session_key: str
    ) -> tuple[Any, dict[str, Any]]:
        if session_key in self.routed_sessions:
            if self.fallback_client is None:
                return None, {}
            return self.fallback_client, {}

        client = self.owner._client  # noqa: SLF001
        if self._current_name != "a2a":
            return client, {}
        return client, {"session_key": session_key}

    async def create_fallback(self) -> Any:
        return await self._ensure_fallback()

    async def _ensure_fallback(self) -> Any:
        async with self._fallback_lock:
            if self.fallback_client is not None:
                if getattr(self.fallback_client, "connected", False):
                    return self.fallback_client
                self.fallback_client = None
                self.routed_sessions.clear()

            client = self.owner._make_ws_client()  # noqa: SLF001
            await client.connect()
            self.bind_fallback_client(client, self.owner._event_bridge_snapshot())  # noqa: SLF001
            self.fallback_client = client
            return client

    def _scoped_bridge(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            self._on_fallback_frame = True
            try:
                return handler(*args, **kwargs)
            finally:
                self._on_fallback_frame = False

        return wrapped

    def _fallback_disconnect_handler(
        self, client: Any
    ) -> Callable[[str, dict[str, Any]], None]:
        def handler(event_name: str, payload: dict[str, Any]) -> None:
            if self.fallback_client is not client:
                return
            routed = list(self.routed_sessions.keys())
            self.routed_sessions.clear()
            self.fallback_client = None
            logger.warning(
                "[a2a-fallback] disconnected: terminating %d routed session(s)",
                len(routed),
            )
            for session_key in routed:
                state = self.owner._sessions.get(session_key)  # noqa: SLF001
                if state is not None and not state.chat_complete.is_set():
                    self.owner._handle_terminal_error(  # noqa: SLF001
                        state,
                        session_key,
                        "fallback connection lost; run outcome unknown",
                        "fallback_disconnect",
                    )

        return handler

    def bind_fallback_client(self, client: Any, event_handlers: dict[str, Any]) -> None:
        """Bridge owner callbacks and attach the isolated disconnect handler."""
        for event_name, handler in event_handlers.items():
            client.on_event(event_name, self._scoped_bridge(handler))
        client.on_disconnect(self._fallback_disconnect_handler(client))

    def has_active_sessions(self) -> bool:
        client = self.owner._client  # noqa: SLF001
        if bool(getattr(client, "has_running_runs", False)):
            return True
        return bool(self.routed_sessions)

    def tracks_session(self, session_key: str) -> bool:
        if session_key in self.routed_sessions:
            return True
        client = self.owner._client  # noqa: SLF001
        inner_tracks = getattr(client, "tracks_session", None)
        return bool(inner_tracks and inner_tracks(session_key))

    def _fuzzy_routed_key(self, session_key: str) -> str | None:
        """Find a routed key with the same exact/contains semantics as
        ``SessionKeyMatcher`` without pretending routed values are session
        states."""
        ignore_case = bool(self.owner._ignore_case)  # noqa: SLF001
        target = session_key.lower() if ignore_case else session_key
        for stored_key in self.routed_sessions:
            candidate = stored_key.lower() if ignore_case else stored_key
            if candidate in target:
                return stored_key
        return None

    def release_terminal_ownership(
        self,
        session_key: str,
        chat_state: Any,
        payload: dict[str, Any],
    ) -> None:
        if not self._on_fallback_frame:
            return
        if chat_state not in {"final", "error"}:
            return
        run_id = payload.get("runId") or payload.get("run_id")
        if isinstance(run_id, str) and run_id.startswith("inject-"):
            return
        if payload.get("stopReason") == "inject":
            return
        reg_key = self._fuzzy_routed_key(session_key)
        if reg_key is None:
            return
        self.routed_sessions.pop(reg_key, None)

    def release_failed_request(self, session_key: str) -> None:
        """Remove fallback ownership when ``chat.send`` is rejected."""
        self.routed_sessions.pop(session_key, None)

    async def close(self) -> None:
        if self.fallback_client is not None:
            try:
                await self.fallback_client.close()
            except Exception:
                logger.exception("A2A fallback close failed")
        self.fallback_client = None
        self._on_fallback_frame = False
        self.routed_sessions.clear()
