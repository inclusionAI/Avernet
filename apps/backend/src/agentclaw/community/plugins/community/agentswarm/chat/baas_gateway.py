"""The platform-API chat transport: one leg over BaaS's SSE stream.

Where :mod:`.engine_ws` speaks the engine's WebSocket frames directly, this
channel asks the platform to do it: one authenticated ``POST`` to the BaaS
gateway's streaming chat endpoint, then consume the SSE frames it emits. The
platform owns bot resolution, session bookkeeping and the engine conversation;
this module owns only what the seam promises — the caller's deadline, cooperative
cancellation, and the failure classification.

Two properties are deliberately copied from the engine channel rather than
reinvented, because the seam's contract is that callers cannot tell the
channels apart:

- **The caller's deadline is authoritative.** Every read is bounded by
  ``min(remaining, slice)``, so a hung stream surfaces as
  :class:`~.base.ChatTimeoutError` instead of an unbounded block, and a
  cancelled leg unwinds within one slice.
- **Cancellation closes the transport and returns the empty result.** No extra
  abort request is issued; the caller distinguishes a cancelled leg by pairing
  the empty result with its own cancel event.

The SSE shape is BaaS's ``DefaultStreamConverter``: every chat frame arrives as
``event: chat`` and is discriminated by ``data.state`` (``delta`` / ``final`` /
``error`` / ``aborted``), heartbeats arrive as SSE comment frames. A stream that
ends without ``final`` is a failure, not a short answer — see
:class:`_StreamEndedEarly`.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import TYPE_CHECKING, Any

import httpx

from agentclaw.community.core.gateway_principal.baas_service_principal import (
    mint_baas_service_app_principal,
)
from agentclaw.community.core.gateway_principal.signer import (
    PrincipalSignerConfig,
)
from agentclaw.community.log import get_logger

from .base import (
    ChatError,
    ChatRateLimitedError,
    ChatRefusedError,
    ChatTimeoutError,
    ChatTransport,
    ChatUnavailableError,
    SessionMode,
)

if TYPE_CHECKING:
    from agentclaw.community.core.agentswarm.domain.models import BotId

logger = get_logger("agentswarm")

#: The streaming chat endpoint on the BaaS gateway surface. JWT-authenticated,
#: which is why this channel mints its own app principal instead of using an
#: API key.
_CHAT_STREAM_PATH = "/openapi/v1/chat/stream"

#: One receive slice inside the stream loop. The loop re-checks the shared
#: cancel event every slice, so a cancelled leg unwinds within one slice rather
#: than after the full remaining deadline.
_RECV_SLICE_SECONDS = 1.0

#: Size of the chunk read from the SSE socket. Bounded so a hostile or broken
#: peer cannot make one read allocate without limit.
_READ_CHUNK_BYTES = 64 * 1024


#: The platform's tenant name — the only one the chat leg ever addresses.
#:
#: One tenant, two spellings: the backend isolates its data under ``teamclaw``
#: while the platform's own tables and the tenant it addresses other services by
#: use ``team_claw``. The chat leg sends the platform's spelling in the minted
#: principal, because the platform reads the tenant from there to look up the
#: resource key and to address the bot API.
#:
#: Kept in code rather than configuration: this is a fixed property of how the
#: two sides name the same tenant, not a deployment choice, and a per-deployment
#: override would let the two drift apart silently — which is exactly the failure
#: it prevents.
_PLATFORM_TENANT = "team_claw"


class _StreamEndedEarly(ChatError):
    """The stream closed without a terminal frame.

    Distinct from a successful empty answer: the platform never told us the leg
    finished, so presenting what we have would publish a truncated turn as a
    complete one.
    """


class BaasGatewayChatTransport(ChatTransport):
    """One chat leg over the BaaS gateway's streaming endpoint.

    Args:
        bot_repo: Resolves ``bot_id`` to the bot record (the same record the
            engine channel reads), used for existence and runtime-binding
            checks before any request is made.
        base_url: The BaaS API base, e.g. ``http://secbaas...:31230``.
        signer: Where the minted token is addressed and what signs it.
        resource_key_prefix: Prefix for the resource key; the full key is
            ``f"{prefix}_{_PLATFORM_TENANT}"``. Encoding the tenant keeps
            ``baas_resource_key``'s single-column unique constraint satisfiable
            while the platform looks rows up by ``(resource_key, tenant)``.
        app_name: Unused for authorization (BaaS reads the resource key row), it
            is stamped into the token's ``app`` block for audit.
        default_timeout: Deadline used when the caller supplies none.
        recv_slice_seconds: Receive-slice length used to re-check cancellation.
        transport: Optional ``httpx.AsyncBaseTransport`` for tests.
    """

    def __init__(
        self,
        *,
        bot_repo: Any,
        base_url: str,
        signer: PrincipalSignerConfig,
        resource_key_prefix: str = "agent_swarm_agent",
        app_name: str = "agent_swarm",
        default_timeout: float = 1800.0,
        recv_slice_seconds: float = _RECV_SLICE_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._bot_repo = bot_repo
        self._base_url = base_url.rstrip("/")
        self._signer = signer
        self._resource_key_prefix = resource_key_prefix
        self._app_name = app_name
        self._default_timeout = default_timeout
        self._recv_slice_seconds = recv_slice_seconds
        self._transport = transport

    # ── the seam ───────────────────────────────────────────────────────────

    @property
    def chat_timeout_seconds(self) -> float:
        return self._default_timeout

    def chat(
        self,
        bot_id: BotId,
        message: str,
        owner_id: str = "",
        session_mode: SessionMode = SessionMode.FRESH,
        session_key: str | None = None,
        cancel_event: threading.Event | None = None,
        timeout_seconds: float | None = None,
        tenant: str = "",
    ) -> str:
        owner = (owner_id or "").strip()
        if not owner:
            raise ChatRefusedError(
                "chat requires the run's authenticated owner "
                "(per-call owner_id); no ambient identity exists"
            )
        deadline = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else self._default_timeout
        )
        return asyncio.run(
            self._chat_once(
                bot_id=bot_id,
                message=message,
                owner=owner,
                session_mode=session_mode,
                session_key=session_key,
                cancel_event=cancel_event,
                timeout_seconds=deadline,
                tenant=tenant,
            )
        )

    # ── the leg ────────────────────────────────────────────────────────────

    def _resolve_target(self, bot_id: BotId, owner: str) -> str:
        """The address the platform expects: ``<bot_id>:<entity_id>``.

        agentswarm's persona ids are bare digests, while the platform addresses a
        bot as ``<real_bot_id>:<entity_id>``. The owner *is* that entity, which
        is also the scope the bot was created under.
        """
        bot = self._bot_repo.get_by_id_and_entity(bot_id, owner)
        if bot is None:
            raise ChatUnavailableError(f"bot {bot_id} not found for chat")
        if not (bot.get("device_id") or "").strip():
            raise ChatUnavailableError(
                f"bot {bot_id} has no runtime binding to chat through"
            )
        return f"{bot_id}:{owner}"

    def _build_payload(
        self,
        *,
        target: str,
        message: str,
        session_mode: SessionMode,
        session_key: str | None,
    ) -> dict[str, Any]:
        """The request body.

        ``FRESH`` carries a fresh business task id, which is how the platform is
        asked for a conversation that does not inherit history. ``CONTINUE``
        carries the caller's session key so the platform resumes that
        conversation.
        """
        metadata: dict[str, Any] = {
            "bot_options": {"lifecycle_stage": "online"},
        }
        if session_mode is SessionMode.CONTINUE and session_key:
            metadata["session_key"] = session_key
        else:
            metadata["biz_task_id"] = f"agentswarm-{threading.get_ident()}-{id(self)}-{_monotonic_ns()}"
        return {"bot_id": target, "message": message, "metadata": metadata}

    async def _chat_once(
        self,
        *,
        bot_id: BotId,
        message: str,
        owner: str,
        session_mode: SessionMode,
        session_key: str | None,
        cancel_event: threading.Event | None,
        timeout_seconds: float,
        tenant: str,  # unused: the platform has one tenant (_PLATFORM_TENANT)
    ) -> str:
        target = self._resolve_target(bot_id, owner)
        resource_key = f"{self._resource_key_prefix}_{_PLATFORM_TENANT}"
        try:
            token = mint_baas_service_app_principal(
                self._signer, _PLATFORM_TENANT, resource_key
            )
        except Exception as exc:  # signing key missing / blank input
            raise ChatUnavailableError(
                f"cannot authenticate to the chat platform for tenant "
                f"{_PLATFORM_TENANT!r}: {exc}"
            ) from exc

        payload = self._build_payload(
            target=target,
            message=message,
            session_mode=session_mode,
            session_key=session_key,
        )
        headers = {
            # This surface authenticates the minted principal, not a bearer
            # credential: BaaS's gateway dependencies read
            # ``Header(alias="x-avernet-principal")`` and answer 40101
            # "Token missing" when it is absent.
            "x-avernet-principal": token,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
        }
        url = f"{self._base_url}{_CHAT_STREAM_PATH}"

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_seconds, read=None),
                transport=self._transport,
            ) as client:
                async with client.stream(
                    "POST", url, json=payload, headers=headers
                ) as response:
                    _raise_for_status(response)
                    return await self._consume(
                        response,
                        cancel_event=cancel_event,
                        timeout_seconds=timeout_seconds,
                    )
        except (ChatError, asyncio.CancelledError):
            raise
        except httpx.TimeoutException as exc:
            raise ChatTimeoutError(
                f"chat platform request for bot {bot_id} did not finish within "
                f"{timeout_seconds:.0f}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise ChatUnavailableError(
                f"chat platform request for bot {bot_id} failed: {exc}"
            ) from exc

    async def _consume(
        self,
        response: httpx.Response,
        *,
        cancel_event: threading.Event | None,
        timeout_seconds: float,
    ) -> str:
        """Read the SSE stream under the caller's deadline.

        Terminates on ``final``; raises on ``error`` / ``aborted``; ignores
        heartbeats and any ``event`` other than ``chat``. Content is assembled
        from ``delta`` frames with the ``final`` body as the authoritative
        version when it carries one.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        accumulated: list[str] = []

        # ``aiter_lines`` is a plain async generator: wrapping ``__anext__`` in
        # ``asyncio.wait_for`` would cancel it on every slice timeout and the
        # iterator would never recover. So the slice bound is applied with
        # ``asyncio.timeout`` around a pending-task dance that keeps the
        # iterator alive across slices.
        iterator = response.aiter_lines()
        pending: asyncio.Task[str] | None = None
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    return ""
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise ChatTimeoutError(
                        f"chat platform stream did not finish within "
                        f"{timeout_seconds:.0f}s"
                    )
                if pending is None:
                    pending = asyncio.ensure_future(iterator.__anext__())
                done, _ = await asyncio.wait(
                    {pending}, timeout=min(remaining, self._recv_slice_seconds)
                )
                if not done:
                    # Slice elapsed with no frame: re-check cancellation and the
                    # deadline, keeping the same pending read.
                    continue
                task, pending = pending, None
                try:
                    line = task.result()
                except StopAsyncIteration:
                    raise _StreamEndedEarly(
                        "chat platform stream ended without a terminal frame; "
                        "refusing to publish a possibly truncated answer"
                    ) from None
                except httpx.HTTPError as exc:
                    raise ChatUnavailableError(
                        f"chat platform stream failed mid-leg: {exc}"
                    ) from exc

                frame = self._parse_line(line)
                if frame is None:
                    continue
                event, data = frame
                if event != "chat":
                    # agent / interaction frames and anything else the platform
                    # adds later are bookkeeping for this channel.
                    continue

                state = data.get("state", "")
                if state == "delta":
                    text = data.get("deltaText")
                    if isinstance(text, str) and text:
                        accumulated.append(text)
                    continue
                if state == "final":
                    final_text = _final_text(data)
                    return final_text if final_text is not None else "".join(accumulated)
                if state == "error":
                    raise ChatRefusedError(
                        "chat platform reported an error: "
                        f"{data.get('errorMessage') or 'unknown'}"
                    )
                if state == "aborted":
                    raise ChatRefusedError("chat was aborted by the platform")
        finally:
            if pending is not None and not pending.done():
                pending.cancel()

        # Unreachable: the loop returns or raises.

    @staticmethod
    def _parse_line(line: str) -> tuple[str, dict[str, Any]] | None:
        """One SSE line to ``(event, data)``, or ``None`` for anything else.

        Only ``event:`` and ``data:`` lines carry information; comments (the
        heartbeat frame) start with ``:`` and are skipped, as are blank lines
        that separate frames.
        """
        if not line or line.startswith(":"):
            return None
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            return value, {}
        if field == "data":
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return None
            return "chat", parsed if isinstance(parsed, dict) else None  # type: ignore[return-value]
        return None


def _monotonic_ns() -> int:
    """A cheap unique suffix for the fresh-conversation task id."""
    return time.monotonic_ns()


def _final_text(data: dict[str, Any]) -> str | None:
    """The reply carried by a ``final`` frame, or ``None`` when it carries none.

    The converter omits ``message`` entirely when the leg produced no content, so
    ``None`` here means "final with no body" rather than "final with an empty
    string".
    """
    message = data.get("message")
    if not isinstance(message, dict):
        return None
    parts: list[str] = []
    for item in message.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "".join(parts)


def _raise_for_status(response: httpx.Response) -> None:
    """Map the platform's HTTP status onto the seam's failure vocabulary."""
    status = response.status_code
    if status < 400:
        return
    if status == 403:
        raise ChatRefusedError(
            "chat platform refused the request: the resource key is not "
            "authorized for this bot (403)"
        )
    if status == 429:
        raise ChatRateLimitedError(
            "chat platform refused the request: the bot is over its concurrency "
            "ceiling (429)"
        )
    if status in (401, 404, 503):
        raise ChatUnavailableError(
            f"chat platform unavailable for this bot (HTTP {status})"
        )
    raise ChatError(f"chat platform request failed with HTTP {status}")


__all__ = ["BaasGatewayChatTransport"]