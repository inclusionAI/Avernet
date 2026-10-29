"""The agentswarm engine-chat transport leg (host_chat).

One cohesive concern out of ``host.py``: everything between the bot
platform and the engine's WebSocket — the bounded chat bridge (one
worker per concurrent chat the pod may carry), the session reset
prelude, ``chat.send`` framing, and the receive-loop deadline that a
per-run ``bot_chat_timeout_seconds`` and the deployment default both
resolve into. ``host.py`` re-exports the public names, so external
callers and tests keep importing from ``agentclaw...agentswarm.host``.
"""
from __future__ import annotations

from agentclaw.community.log import get_logger

logger = get_logger("agentswarm")

import asyncio
import atexit
import concurrent.futures
import json
import ssl
import threading
import uuid

# Upper bound on one engine chat round trip. It follows the swarm's own
# per-attempt task budget (``orchestration.per_attempt_timeout``, 1800s
# after the durable-persona raise; setup 900 + chat 1800 = 2700 still fits
# the 3600s slice room): a long-running coding/review turn may legitimately
# need the whole attempt budget, so capping the bridge at the old 300s
# slice cadence killed healthy chats mid-turn and burned the retry budget
# on fresh personas + fresh authorization gates. Keeps the blocked bridge
# thread from waiting forever on a hung engine WebSocket, and bounds every
# receive slice in the recv loop so a cancelled chat unwinds within one
# slice even while a socket recv hangs.
_CHAT_TIMEOUT_SECONDS = 1800.0

# One receive slice inside the recv loop. Each slice sits well under the
# conversation deadline; the loop re-checks the shared cancel event every
# slice, so a chat cancelled from the asyncio side unwinds within one
# slice of the set() rather than after the full remaining deadline.
_RECV_SLICE_SECONDS = 1.0

# Upper bound on one sessions.reset round (send + consuming its res),
# the fresh-conversation prelude before every chat.send. The reset is
# deliberately fail-soft: a slow or broken answer only warns — a
# reset that never completes must not hold the chat leg (or its bridge
# worker) hostage; the receive slices inside keep it cooperative with
# the shared cancel event just like the chat loop.
_SESSION_RESET_SECONDS = 10.0
# One worker per concurrent engine chat the pod may carry: the default for
# direct construction; the composition root sizes the bridge from
# ``agentswarm.executor.max_platform_workers``.
_DEFAULT_MAX_PLATFORM_WORKERS = 4

# Engine chat transport: the in-pod adapter channel and its zero-check
# machine-caller token (OPEN_API:<kind>:<app>; no certified token, and the
# check itself fails open on its own infrastructure errors).
_ENGINE_WS_PORT = 20003
_ENGINE_WS_PATH = "/api/openclaw/ws"
_ZERO_CHECK_OPEN_API_TOKEN = "OPEN_API:app:agentswarm"

# Conversation addressing for a chat whose caller carries none: the
# engine-served fixed session. The engine only serves sessions it knows
# (a chat.send on a novel key is refused with no events), so the
# reset-before-chat prelude resets this same key — the engine's blessed
# fresh-conversation mechanism — instead of minting per-attempt keys.
_DEFAULT_SESSION_KEY = "agent:main:default"


# Module-scoped chat bridge (async-run-and-resume D-B): one bounded pool
# whose workers carry sync ``BotPlatform.chat`` legs (one asyncio.run loop
# per chat) off the swarm's event loop. Workers return to the pool after
# every chat — cancelled or timed-out legs included — so repeated timeouts
# cannot grow the live thread set past ``max_platform_workers``; the pool
# only ever shuts down at process exit (atexit → ``shutdown_chat_bridge``).
# The laziness lives inside a holder instance, not module globals: the
# lazy double-checked-locking singleton shape is forbidden by
# tests/community/architecture/test_no_dcl_singletons.
class _ChatBridge:
    """First-use-created chat pool; size pinned before the pool ever runs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._max_workers: int | None = None
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None

    def set_max_workers(self, max_workers: int) -> None:
        """Called from ``AvernetBotPlatform.__init__`` (the DI provider passes
        ``agentswarm.executor.max_platform_workers``); first call wins."""
        with self._lock:
            if self._executor is None:
                self._max_workers = max(1, int(max_workers))

    def executor(self) -> concurrent.futures.ThreadPoolExecutor:
        with self._lock:
            if self._executor is None:
                self._executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=self._max_workers or _DEFAULT_MAX_PLATFORM_WORKERS,
                    thread_name_prefix="agentswarm-chat",
                )
            return self._executor

    def shutdown(self) -> None:
        """Process-exit close hook; during operation nothing calls this:
        workers return to the pool after every leg, cancelled or not."""
        with self._lock:
            executor, self._executor = self._executor, None
            self._max_workers = None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)


_CHAT_BRIDGE = _ChatBridge()


def set_chat_bridge_max_workers(max_workers: int) -> None:
    _CHAT_BRIDGE.set_max_workers(max_workers)


def chat_bridge_executor() -> concurrent.futures.ThreadPoolExecutor:
    return _CHAT_BRIDGE.executor()


def shutdown_chat_bridge() -> None:
    _CHAT_BRIDGE.shutdown()


atexit.register(shutdown_chat_bridge)


class ChatTimeoutError(RuntimeError):
    """The engine chat leg did not answer within ``_CHAT_TIMEOUT_SECONDS``.

    Raised by ``AvernetBotPlatform.chat`` when the bridge leg exceeds the
    bounded wait: the recv loop's own deadline fires and the caller's
    thread is freed with this error. The bridge thread is never abandoned
    — the same deadline unwinds the recv loop inside the pool worker, and
    the worker returns to the bridge pool. A chat cancelled through its
    ``cancel_event`` (slice lost from the asyncio side) unwinds the same
    way, within one receive slice.
    """


class ChatLegMixin:
    """The engine chat leg of ``AvernetBotPlatform`` (see ``host.py``):
    sync-out by the BotPlatform SPI contract, async-in over the bridge
    worker's own loop, deadline-bounded by ``_CHAT_TIMEOUT_SECONDS`` —
    or the per-call ``timeout_seconds`` the run options resolved."""

    def chat(self, bot_id: BotId, message: str, owner_id: str = "",
             session_key: str | None = None,
             cancel_event: threading.Event | None = None,
             timeout_seconds: float | None = None) -> str:
        # Sync-out by SPI contract; async-in in the bridge worker that
        # carries this call: the engine leg runs its own asyncio.run loop
        # here and every wait inside it is bounded — the recv loop's
        # deadline and receive slices, either of which (or the caller's
        # cancel_event) unwinds the worker back to the bridge pool
        # instead of abandoning it. ``timeout_seconds`` is the per-call
        # deadline from the run's own options; absent, the platform's
        # deployment-configured default (executor.bot_chat_timeout_seconds)
        # governs.
        owner = (owner_id or "").strip()
        if not owner:
            raise RuntimeError(
                "provisioning requires the run's authenticated owner "
                "(per-call owner_id); no ambient identity exists"
            )
        deadline = (float(timeout_seconds)
                    if timeout_seconds is not None
                    else self._chat_timeout_seconds)
        try:
            return asyncio.run(self._chat_once(
                bot_id, message, owner, session_key, cancel_event,
                timeout_seconds=deadline))
        except TimeoutError as exc:
            raise ChatTimeoutError(
                f"engine chat for bot {bot_id} did not finish within "
                f"{deadline:.0f}s"
            ) from exc

    @property
    def chat_timeout_seconds(self) -> float:
        """The deployment-configured chat deadline — the fallback the
        per-run options resolve against."""
        return self._chat_timeout_seconds

    def _engine_endpoint(self, bot_id: BotId, owner: str) -> tuple[str, str]:
        """Resolve the bot's chat endpoint via BaaS ws-info.

        The sandbox proxy only resolves targets the BaaS provider registry
        knows (bot-uuid-minted targets close the WS 1008 in proxypass_ws),
        so the connection pair always comes from the canonical
        ``/api/v1/bots/{uuid}/ws-info`` resolution — never minted here.
        """
        bot = self._bot_repo.get_by_id_and_entity(bot_id, owner)
        if bot is None:
            raise RuntimeError(f"bot {bot_id} not found for chat")
        bot_uuid = (bot.get("device_id") or "").strip()
        if not bot_uuid:
            raise RuntimeError(
                f"bot {bot_id} has no runtime binding to chat through"
            )
        try:
            info = self._baas_service.get_ws_info_by_bot_uuid(
                bot_uuid=bot_uuid,
                port=_ENGINE_WS_PORT,
                path=_ENGINE_WS_PATH,
            )
        except Exception as exc:
            logger.warning(
                "[agentswarm][host-platform] baas ws-info failed for "
                "bot=%s uuid=%s: %s", bot_id, bot_uuid, exc,
            )
            raise RuntimeError(
                f"bot {bot_id} has no runtime binding to chat through"
            ) from exc
        ws_url = (getattr(info, "ws_url", "") or "").strip()
        token = (getattr(info, "token", "") or "").strip()
        if not ws_url or not token:
            raise RuntimeError(
                f"bot {bot_id} has no runtime binding to chat through"
            )
        return ws_url, token

    async def _chat_once(self, bot_id: BotId, message: str, owner: str,
                         session_key: str | None,
                         cancel_event: threading.Event | None,
                         timeout_seconds: float | None = None) -> str:
        ws_url, token = self._engine_endpoint(bot_id, owner)
        ws = await self._connect_engine(ws_url, token)
        try:
            # Reset-before-chat: the upcoming chat.send addresses one
            # session key, and the engine's blessed fresh-conversation
            # mechanism is sessions.reset on that key — a retried or
            # respawned leg must not inherit the orphaned turn's history.
            # Fail-soft inside: a failed reset only warns and the chat
            # proceeds.
            await self._reset_session(
                ws, session_key or _DEFAULT_SESSION_KEY, cancel_event)
            return await self._chat_send(
                ws, message, token, session_key, cancel_event,
                timeout_seconds=timeout_seconds)
        finally:
            await ws.close()

    async def _reset_session(self, ws, session_key: str,
                             cancel_event: threading.Event | None) -> None:
        """One fail-soft ``sessions.reset`` round for the conversation the
        upcoming chat.send will address. The whole round trip is bounded
        (``asyncio.wait_for`` over ``_SESSION_RESET_SECONDS``); on timeout,
        transport error, or a refused reset the reset is abandoned with a
        warning and the chat proceeds — prior-turn context may be
        inherited, which is an answer quality risk, never a liveness one.
        A cancelled chat unwinds the round quietly: its caller stopped
        waiting, so there is nothing left to reset for."""
        reset_id = str(uuid.uuid4())
        reset_frame = {
            "type": "req",
            "id": reset_id,
            "method": "sessions.reset",
            "params": {
                "key": session_key,
                "x-iam-token": _ZERO_CHECK_OPEN_API_TOKEN,
            },
        }
        try:
            await asyncio.wait_for(
                self._reset_session_round(ws, reset_frame, reset_id,
                                          cancel_event),
                timeout=_SESSION_RESET_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[agentswarm][host-platform] session reset failed; chat "
                "proceeds, prior-turn context may be inherited "
                "(session=%s): %s", session_key, exc,
            )

    async def _reset_session_round(self, ws, reset_frame: dict,
                                   reset_id: str,
                                   cancel_event: threading.Event | None
                                   ) -> None:
        """Send one sessions.reset frame and consume its res. The recv
        wait runs in the chat's receive slices so a cancelled chat unwinds
        the reset within one slice; frames that are not this reset's res
        (stale events, unrelated responses) are ignored until it lands or
        the outer bound fires."""
        await ws.send(json.dumps(reset_frame))
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return
            try:
                raw = await asyncio.wait_for(
                    ws.recv(), timeout=_RECV_SLICE_SECONDS)
            except asyncio.TimeoutError:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if data.get("type") != "res" or data.get("id") != reset_id:
                continue
            if not data.get("ok", False):
                payload = data.get("payload")
                raise RuntimeError(
                    f"engine refused the reset: {payload!r}")
            return

    async def _connect_engine(self, ws_url: str, token: str) -> Any:
        """Connect+handshake with bounded retry (15/30/60s)."""
        for i in range(4):
            try:
                return await self._connect_engine_once(ws_url, token)
            except ConnectionError as e:
                if i == 3:
                    raise
                logger.warning("[agentswarm][host] engine connect %d/4: %s", i + 1, e)
                await asyncio.sleep(15.0 * (2 ** i))

    async def _connect_engine_once(self, ws_url: str, token: str) -> Any:
        import websockets

        ssl_context = None
        if ws_url.startswith("wss://"):
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        try:
            ws = await asyncio.wait_for(
                websockets.connect(
                    ws_url,
                    additional_headers={"x-proxypass-token": token},
                    ssl=ssl_context,
                    max_size=2 * 1024 * 1024,
                ),
                timeout=30,
            )
        except Exception as e:
            raise ConnectionError(
                f"engine websocket connect failed ({ws_url}): {e}"
            ) from e

        connect_frame = {
            "type": "req",
            "id": str(uuid.uuid4()),
            "method": "connect",
            "params": {
                "minProtocol": 3,
                "maxProtocol": 3,
                "client": {
                    "id": "cli",
                    "version": "1.0.0",
                    "platform": "MacIntel",
                    "mode": "cli",
                },
                "role": "operator",
                "x-moltis-mcp-token": token,
                "scopes": [
                    "operator.admin",
                    "operator.read",
                    "operator.write",
                ],
            },
        }
        try:
            await ws.send(json.dumps(connect_frame))
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            resp = json.loads(raw)
            if not resp.get("ok", False):
                await ws.close()
                raise ConnectionError(
                    f"engine handshake failed: "
                    f"{resp.get('error', {}).get('message', 'unknown error')}"
                )
            return ws
        except ConnectionError:
            raise
        except Exception as e:
            await ws.close()
            raise ConnectionError(f"engine handshake failed: {e}") from e

    async def _chat_send(self, ws, message: str, token: str,
                         session_key: str | None,
                         cancel_event: threading.Event | None,
                         timeout_seconds: float | None = None) -> str:
        """One chat.send round: the adapter's zero-check gate requires an
        x-iam-token on every frame. OPEN_API:<kind>:<app> is the form it
        accepts for machine callers (no certified token; the check fails
        open on its own infrastructure errors). ``session_key`` names a
        known engine conversation — the engine-served default when this
        call carries none; a refuse answer (ok false or accepted false)
        comes back as the res for the outstanding request id and fails the
        leg immediately instead of eating the receive deadline (the live
        engine never events a refused send)."""
        request_id = str(uuid.uuid4())
        chat_session_key = session_key or _DEFAULT_SESSION_KEY
        request_frame = {
            "type": "req",
            "id": request_id,
            "method": "chat.send",
            "params": {
                "sessionKey": chat_session_key,
                "message": message,
                "deliver": False,
                "idempotencyKey": request_id,
                "x-iam-token": _ZERO_CHECK_OPEN_API_TOKEN,
            },
        }
        await ws.send(json.dumps(request_frame))

        # A cancelled chat exits the loop with the empty sentinel: its
        # caller stopped waiting when the asyncio side was cancelled, so
        # the reply is discarded by definition, and quiet return keeps the
        # unwinding worker's bridge future free of an unretrieved
        # exception that only the dead await would have consumed.
        deadline = asyncio.get_event_loop().time() + (
            float(timeout_seconds) if timeout_seconds is not None
            else self._chat_timeout_seconds)
        accumulated_text = ""
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return ""
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(
                    f"[host-platform][chat] timeout waiting for final: id={request_id}"
                )
            try:
                raw = await asyncio.wait_for(
                    ws.recv(),
                    timeout=min(remaining, _RECV_SLICE_SECONDS),
                )
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                raise ConnectionError(f"[host-platform][chat] ws closed: {e}") from e

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "res" and data.get("id") == request_id:
                # The res for this very chat.send: a healthy send answers
                # ok/accepted true (the ack before the events); ok false
                # or accepted false is the engine's refusal, and no
                # events follow — fail now, not at the deadline. Other res
                # frames (late answers to earlier requests, say) keep
                # being ignored.
                payload = data.get("payload")
                if (not data.get("ok", False)
                        or (isinstance(payload, dict)
                            and payload.get("accepted") is False)):
                    detail = raw if len(raw) <= 300 else raw[:297] + "..."
                    raise RuntimeError(
                        f"[host-platform][chat] engine refused chat.send "
                        f"for session {chat_session_key}: {detail}"
                    )
                continue
            if data.get("type") != "event" or data.get("event") != "chat":
                continue
            payload = data.get("payload", {})
            state = payload.get("state", "")
            if state == "final":
                for c in (payload.get("message", {}) or {}).get("content", []):
                    if c.get("type") == "text":
                        accumulated_text = c.get("text", "")
                return accumulated_text
            if state == "error":
                raise RuntimeError(
                    f"[host-platform][chat] agent error: "
                    f"{payload.get('errorMessage', 'unknown')}"
                )
            if state == "aborted":
                raise RuntimeError("[host-platform][chat] chat aborted")
