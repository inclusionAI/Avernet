"""Connection ownership and operation forwarding shared by chat transports.

Only implementations use this helper. The facade depends on ChatTransport,
not this class, and never receives a wire client.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal, cast

from secbaas.community.api.bot_interaction import InteractionResolution
from secbaas.community.logger import get_logger

from ._async_chat_client_transport import ChatTransportCallbacks
from ._chat_errors import ChatRequestError, NotConnectedError
from ._interaction_protocol import EngineInteractionResolveExchange

logger = get_logger("core-bot-run")


class ChatTransportBase:
    def __init__(
        self,
        uri: str,
        *,
        headers: dict[str, str] | None = None,
        client_id: str | None = None,
        client_version: str = "1.0.0",
    ) -> None:
        self.uri = uri
        self.headers = dict(headers or {})
        self.client_id = client_id or f"client-{uuid.uuid4().hex[:8]}"
        self.client_version = client_version
        self._client: Any | None = None
        # None is the pre-bind lifecycle state, never an omitted dependency.
        self._callbacks: ChatTransportCallbacks | None = None

    def bind_events(self, callbacks: ChatTransportCallbacks) -> None:
        if self._client is not None:
            raise RuntimeError("Bind transport events before connecting")
        # Do not share a mutable registry with the consumer.
        self._callbacks = ChatTransportCallbacks(
            events=dict(callbacks.events),
            on_disconnect=callbacks.on_disconnect,
            on_sessions_failed=callbacks.on_sessions_failed,
        )

    def _event_callbacks(self) -> ChatTransportCallbacks:
        if self._callbacks is None:
            raise RuntimeError("Transport events must be bound before connecting")
        return self._callbacks

    @property
    def is_connected(self) -> bool:
        return self._client is not None and bool(self._client.connected)

    @property
    def transport_name(self) -> str:
        raise NotImplementedError

    def refresh_outbound_auth(self, headers: dict[str, str] | None) -> None:
        token = (headers or {}).get("x-proxypass-token", "")
        if token:
            self.headers["x-proxypass-token"] = token

    async def connect(self) -> dict[str, Any]:
        raise NotImplementedError

    async def _connect_client(self, client: Any) -> dict[str, Any]:
        if self._client is not None:
            raise RuntimeError("Already connected")
        callbacks = self._event_callbacks()
        for event, handler in callbacks.events.items():
            client.on_event(event, handler)

        def disconnected(event: str, payload: dict[str, Any]) -> None:
            if self._client is client:
                callbacks.on_disconnect(event, payload)

        client.on_disconnect(disconnected)
        self._client = client
        try:
            return cast("dict[str, Any]", await client.connect())
        except BaseException:
            # Includes cancelled handshakes: no candidate may escape ownership.
            try:
                await self._close_main()
            except Exception:
                logger.exception("Failed to dispose of chat handshake candidate")
            raise

    async def _close_main(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.close()

    async def reconnect(self) -> dict[str, Any]:
        try:
            await self._close_main()
        except Exception:
            logger.warning("Failed to close previous chat connection", exc_info=True)
        return await self.connect()

    async def close(self) -> None:
        await self._close_main()

    def _require_client(self) -> Any:
        if self._client is None:
            raise NotConnectedError("engine websocket is disconnected")
        return self._client

    async def _request_client(
        self,
        *,
        session_key: str,
        attachments: list[Any] | None,
        request_kind: Literal["send", "inject"],
    ) -> Any:
        return self._require_client()

    def _send_rejected(self, session_key: str) -> None:
        """Implementation-local cleanup only for an explicitly rejected request."""

    async def send(
        self,
        *,
        session_key: str,
        message: str,
        auth_token: str | None,
        app_id: str | None,
        timeout_ms: int | None,
        chat_metadata: dict[str, str] | None,
        attachments: list[Any] | None,
    ) -> dict[str, Any]:
        client = await self._request_client(
            session_key=session_key, attachments=attachments, request_kind="send"
        )
        try:
            result = cast(
                "dict[str, Any]",
                await client.chat_send(
                    session_key=session_key,
                    message=message,
                    auth_token=auth_token,
                    app_id=app_id,
                    timeout_ms=timeout_ms,
                    chat_metadata=chat_metadata,
                    attachments=attachments,
                ),
            )
        except ChatRequestError:
            self._send_rejected(session_key)
            raise
        if not result.get("ok"):
            self._send_rejected(session_key)
        return result

    async def inject(
        self,
        *,
        session_key: str,
        message: str,
        auth_token: str | None,
        chat_metadata: dict[str, str] | None,
        attachments: list[Any] | None,
    ) -> dict[str, Any]:
        client = await self._request_client(
            session_key=session_key, attachments=attachments, request_kind="inject"
        )
        return cast(
            "dict[str, Any]",
            await client.chat_inject(
                session_key=session_key,
                message=message,
                auth_token=auth_token,
                chat_metadata=chat_metadata,
                attachments=attachments,
            ),
        )

    def _abort_client(self, session_key: str) -> Any:
        return self._require_client()

    async def abort(self, *, session_key: str, run_id: str | None) -> dict[str, Any]:
        return cast(
            "dict[str, Any]",
            await self._abort_client(session_key).chat_abort(
                session_key=session_key, run_id=run_id
            ),
        )

    def can_resolve_interaction(self, session_key: str) -> bool:
        return self.is_connected

    async def resolve_interaction(
        self,
        *,
        session_key: str,
        interaction_id: str,
        resolution: InteractionResolution,
    ) -> EngineInteractionResolveExchange:
        return cast(
            "EngineInteractionResolveExchange",
            await self._require_client().interaction_resolve(
                interaction_id=interaction_id, resolution=resolution
            ),
        )

    def has_active_sessions(self) -> bool:
        return bool(getattr(self._client, "has_running_runs", False))

    def tracks_session(self, session_key: str) -> bool:
        tracks = getattr(self._client, "tracks_session", None)
        return bool(tracks and tracks(session_key))
