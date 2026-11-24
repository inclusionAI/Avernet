"""Process-local chat.send serialization shared by default and token clients."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
import weakref
from collections import OrderedDict
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable

from engine.community.openclaw.client.chat_dispatch import ChatDispatch, supports_dispatch_wait
from engine.community.openclaw.client.chat_stream import chat_stream_events
from engine.community.openclaw.protocol import ResponseFrame

if TYPE_CHECKING:
    from engine.community.openclaw.client.gateway_client import OpenClawGatewayClient

log = logging.getLogger("openclaw-client")
MAX_SESSIONS = 1024
MAX_WAITERS = 32
MAX_BUFFER_BYTES = 1_572_864
DEFAULT_TIMEOUT = 20 * 60
ACK_DELIVERY_TIMEOUT = 5
COMPLETION_CACHE_SECONDS = 5 * 60


@dataclass
class SessionSlot:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0
    blocked: bool = False


_slots: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_completed: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_workers: dict[asyncio.Task, OpenClawGatewayClient] = {}
_dispatches: dict[asyncio.Task, tuple[tuple[str, str], ChatDispatch]] = {}
_retirements: set[asyncio.Task] = set()


def _error(code: str, message: str) -> dict[str, Any]:
    return {"state": "error", "errorCode": code, "errorMessage": message, "_source_event": "chat"}


def _session_key(client: OpenClawGatewayClient, session: str) -> tuple[str, str]:
    return client._config.gateway_url, session.strip().lower().removeprefix("agent:main:")


async def cancel_pending(client: OpenClawGatewayClient, session: str, run: str | None) -> list[str]:
    """Cancel only unsent work; active runs still use Gateway chat.abort."""
    matches = [(task, dispatch) for task, (key, dispatch) in _dispatches.items()
               if key == _session_key(client, session) and not dispatch.sent
               and (run is None or dispatch.run == run)]
    for task, _ in matches:
        task.cancel()
    await asyncio.gather(*(task for task, _ in matches), return_exceptions=True)
    return [dispatch.run for _, dispatch in matches]


async def retire_client(client: OpenClawGatewayClient) -> None:
    """An inbound disconnect must not destroy a detached dispatch's observer."""
    async def retire() -> None:
        tasks = [task for task, owner in _workers.items() if owner is client]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await client.disconnect()

    task = asyncio.create_task(retire())
    _retirements.add(task)
    def finished(done: asyncio.Task) -> None:
        _retirements.discard(done)
        if not done.cancelled() and done.exception():
            log.warning("[chat_stream][retire_failed] reason=%s", type(done.exception()).__name__)
    task.add_done_callback(finished)
    await asyncio.shield(task)


async def serialized_chat(
    client: OpenClawGatewayClient,
    session_key: str,
    message: str,
    timeout_ms: int | None = None,
    idempotency_key: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    on_ack: Callable[[ResponseFrame], Awaitable[None]] | None = None,
    *,
    max_pre_ack_bytes: int,
) -> AsyncIterator[dict[str, Any]]:
    # Keep stop commands usable while a foreground turn owns the session.
    if not supports_dispatch_wait(client) or message.strip().lower() in {"/stop", "/abort"}:
        async with aclosing(chat_stream_events(
            client, session_key, message, timeout_ms, idempotency_key, attachments,
            on_ack, max_pre_ack_bytes=max_pre_ack_bytes,
        )) as stream:
            async for event in stream:
                yield event
        return

    loop = asyncio.get_running_loop()
    slots = _slots.setdefault(loop, {})
    completed = _completed.setdefault(loop, OrderedDict())
    # COSEC: Do not coalesce different agents. Only main has an unprefixed alias.
    key = _session_key(client, session_key)
    if key not in slots and len(slots) >= MAX_SESSIONS:
        yield _error("CHAT_QUEUE_FULL", "Too many active or unconfirmed chat sessions")
        return
    slot = slots.setdefault(key, SessionSlot())
    if slot.users >= MAX_WAITERS:
        yield _error("CHAT_QUEUE_FULL", "Too many pending requests for this session")
        return
    queued = slot.users > 0
    slot.users += 1
    completion = ChatDispatch(client, session_key, idempotency_key or uuid.uuid4().hex, message)
    output: asyncio.Queue = asyncio.Queue()
    attached = True
    entered = False
    queue_acknowledged = False
    buffered = 0
    budget = timeout_ms / 1000 if timeout_ms else DEFAULT_TIMEOUT

    def publish(event: dict[str, Any]) -> None:
        nonlocal attached, buffered
        if not attached:
            return
        size = len(json.dumps(event, ensure_ascii=False).encode("utf-8"))
        if buffered + size > MAX_BUFFER_BYTES:
            attached = False
            while not output.empty():
                output.get_nowait()
            output.put_nowait((_error("CHAT_CONSUMER_SLOW", "Chat consumer fell behind; execution continues"), 0))
            output.put_nowait((None, 0))
            return
        buffered += size
        output.put_nowait((event, size))

    async def acknowledge(response: ResponseFrame) -> None:
        nonlocal attached
        # A cached ACK is not proof of completion. Only reuse our own recent
        # verified result; unknown replays must not unlock a possibly active turn.
        finished_at = completed.get((*key, completion.run), float("-inf"))
        completion.cached_confirmed = loop.time() - finished_at < COMPLETION_CACHE_SECONDS
        if on_ack is None and not response.ok:
            error = response.error
            publish({**_error(error.code if error else "CHAT_REJECTED",
                             error.message if error else "OpenClaw rejected the request"),
                     "runId": completion.run, "sessionKey": session_key})
            return
        if queue_acknowledged:
            # The request already has its one ACK. Report a later rejection or
            # cached replay through the stream, never a second request response.
            if not response.ok:
                error = response.error
                publish({**_error(error.code if error else "CHAT_REJECTED",
                                 error.message if error else "OpenClaw rejected the queued request"),
                         "runId": completion.run, "sessionKey": session_key})
            elif (response.payload or {}).get("status") == "ok":
                publish({**_error("CHAT_REQUEST_CACHED", "Request already exists; inspect the original run"),
                         "runId": completion.run, "sessionKey": session_key})
            return
        if attached and on_ack is not None:
            try:
                await asyncio.wait_for(on_ack(response), timeout=min(budget, ACK_DELIVERY_TIMEOUT))
            except Exception:
                # Downstream delivery failure is not an upstream cancellation.
                attached = False
                output.put_nowait((None, 0))

    async def run() -> None:
        nonlocal entered, queue_acknowledged
        entered = True
        acquired = False
        try:
            if queued and not slot.blocked and on_ack is not None:
                # BaaS waits at most 120 s for this response. A bounded local
                # queue admission is not execution completion or a Gateway ACK.
                await asyncio.wait_for(on_ack(ResponseFrame.ok_response(
                    completion.run, {"runId": completion.run, "status": "queued", "accepted": True},
                )), timeout=min(budget, ACK_DELIVERY_TIMEOUT))
                queue_acknowledged = True
            await asyncio.wait_for(slot.lock.acquire(), timeout=budget)
            acquired = True
            if slot.blocked:
                publish(_error("DISPATCH_UNCONFIRMED", "Previous execution is unconfirmed; do not automatically retry"))
                return
            async with aclosing(chat_stream_events(
                client, session_key, message, timeout_ms, completion.run, attachments,
                acknowledge, completion=completion, max_pre_ack_bytes=max_pre_ack_bytes,
            )) as stream:
                async for event in stream:
                    publish(event)
        except asyncio.CancelledError:
            if completion.sent:
                raise
            publish({"state": "aborted", "runId": completion.run, "sessionKey": session_key,
                     "_source_event": "chat"})
        except asyncio.TimeoutError:
            publish(_error(
                "DISPATCH_UNCONFIRMED" if completion.sent else "CHAT_QUEUE_TIMEOUT",
                "Execution is unconfirmed; do not retry" if completion.sent
                else "Timed out waiting for this session; request was not sent",
            ))
        except Exception as exc:
            log.warning("[chat_stream][dispatch_failed] runId=%s reason=%s", completion.run, type(exc).__name__)
            publish(_error("DISPATCH_UNCONFIRMED", "Chat execution could not be confirmed; do not automatically retry"))
        finally:
            if acquired:
                slot.blocked = slot.blocked or (completion.sent and not completion.confirmed)
                if completion.started and completion.confirmed:
                    completed[(*key, completion.run)] = loop.time()
                    completed.move_to_end((*key, completion.run))
                    while len(completed) > MAX_SESSIONS:
                        completed.popitem(last=False)
                slot.lock.release()
            slot.users -= 1
            if not slot.users and not slot.blocked:
                slots.pop(key, None)
            output.put_nowait((None, 0))

    worker = asyncio.create_task(run())
    _workers[worker] = client
    _dispatches[worker] = key, completion
    def worker_finished(task: asyncio.Task) -> None:
        _workers.pop(task)
        _dispatches.pop(task)
        if not entered:
            # Cancellation can win before the coroutine gets its first tick.
            slot.users -= 1
            if not slot.users and not slot.blocked:
                slots.pop(key, None)
            publish({"state": "aborted", "runId": completion.run, "sessionKey": session_key,
                     "_source_event": "chat"})
            output.put_nowait((None, 0))
    worker.add_done_callback(worker_finished)
    try:
        while True:
            event, size = await output.get()
            buffered -= size
            if event is None:
                break
            yield event
    finally:
        attached = False
        while not output.empty():
            output.get_nowait()
        if not completion.sent and not queue_acknowledged:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
