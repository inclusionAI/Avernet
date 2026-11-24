from __future__ import annotations

import asyncio
from contextlib import aclosing
from unittest.mock import AsyncMock

import pytest

from engine.community.openclaw.client import chat_dispatch, chat_serialization
from engine.community.openclaw.client.chat_dispatch import supports_dispatch_wait
from engine.community.openclaw.client.tests.test_gateway_client import _make_client, _fire_event
from engine.community.openclaw.protocol import HelloOk, ResponseFrame


async def eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.001)


@pytest.fixture(autouse=True)
async def cleanup(monkeypatch):
    monkeypatch.setattr(chat_dispatch, "POLL_SECONDS", .001)
    yield
    workers = list(chat_serialization._workers)
    for task in workers:
        task.cancel()
    await asyncio.gather(*workers, return_exceptions=True)
    chat_serialization._slots.clear()
    chat_serialization._completed.clear()


class Gateway:
    def __init__(self):
        self.clients = []
        self.sent = []
        self.complete = {}
        self.polled = []
        self.started = True
        self.send_failure = None
        self.wait_failure = None

    def client(self):
        client = _make_client()
        client._hello = HelloOk.from_dict({
            "protocol": 3, "server": {"version": "2026.5.12", "connId": "test"},
            "features": {"methods": ["chat.send", "agent.wait"], "events": ["agent", "chat"]},
        })
        self.clients.append(client)

        async def send(method, params, **kwargs):
            if method == "chat.send":
                run = params["idempotencyKey"]
                self.sent.append(run)
                if self.send_failure:
                    raise self.send_failure
                if self.started:
                    self.agent(run, "lifecycle", phase="start")
                return ResponseFrame.ok_response("ack", {"runId": run, "status": "started"})
            assert method == "agent.wait", "Generation must remain chat.send"
            assert params["timeoutMs"] == 0, "Positive waits can observe intermediate lifecycle end"
            run = params["runId"]
            self.polled.append(run)
            if self.wait_failure:
                raise self.wait_failure
            status = self.complete.get(run)
            return ResponseFrame.ok_response("wait", {
                "runId": run, "status": status or "timeout", **({"endedAt": 42} if status else {}),
            })
        client.send_request = send
        return client

    def event(self, name, payload):
        for client in self.clients:
            _fire_event(client, name, dict(payload))

    def agent(self, run, stream, **data):
        self.event("agent", {"runId": run, "stream": stream, "data": data})

    def final(self, run, text=None):
        self.event("chat", {"runId": run, "state": "final", **({"message": {
            "role": "assistant", "content": [{"type": "text", "text": text}],
        }} if text is not None else {})})

    def finish(self, run, text="answer", status="ok"):
        self.agent(run, "assistant", text=text)
        self.agent(run, "lifecycle", phase="end")
        self.final(run, text)
        self.complete[run] = status


async def collect(client, run, session="sk", message="hello", seen=None, **kwargs):
    seen = seen if seen is not None else []
    async with aclosing(client.chat_stream(session, message, idempotency_key=run, **kwargs)) as stream:
        async for event in stream:
            seen.append(event)
    return seen


@pytest.mark.parametrize("final_first", [True, False])
async def test_compaction_holds_nonempty_final_and_lifecycle_until_dispatch_completed(final_first):
    gateway = Gateway()
    seen = []
    task = asyncio.create_task(collect(gateway.client(), "a", seen=seen))
    await eventually(lambda: gateway.sent)
    if not final_first:
        gateway.agent("a", "compaction", phase="end", willRetry=True)
    gateway.agent("a", "lifecycle", phase="end")
    gateway.final("a", "intermediate answer")
    if final_first:
        gateway.agent("a", "compaction", phase="end", willRetry=True)
    await eventually(lambda: gateway.polled)
    assert not task.done()
    assert not any(e.get("state") == "final" or e.get("data", {}).get("phase") == "end"
                   and e.get("stream") == "lifecycle" for e in seen)
    # Replayed turns can have agent text but no second chat.final.
    gateway.agent("a", "assistant", text="after compaction")
    gateway.complete["a"] = "ok"
    result = await asyncio.wait_for(task, 2)
    assert result[-1]["message"]["content"][0]["text"] == "after compaction"
    assert sum(e.get("state") == "final" for e in result) == 1
    assert not any(gateway.clients[0]._event_listeners.values())


async def test_same_session_cross_clients_queue_until_complete_not_early_final():
    gateway = Gateway()
    a, b, c = [gateway.client() for _ in range(3)]
    first = asyncio.create_task(collect(a, "a", session="agent:main:sk"))
    await eventually(lambda: gateway.sent == ["a"])
    gateway.final("a")
    await eventually(lambda: gateway.polled)
    second = asyncio.create_task(collect(b, "b"))
    third = asyncio.create_task(collect(c, "c"))
    await asyncio.sleep(.02)
    assert gateway.sent == ["a"]
    gateway.finish("a")
    await first
    await eventually(lambda: gateway.sent == ["a", "b"])
    gateway.finish("b")
    await second
    await eventually(lambda: gateway.sent == ["a", "b", "c"])
    gateway.finish("c")
    assert (await third)[-1]["runId"] == "c"


@pytest.mark.parametrize("session", ["other", "agent:other:sk"])
async def test_other_sessions_and_agents_are_not_serialized(session):
    gateway = Gateway()
    client = gateway.client()
    a = asyncio.create_task(collect(client, "a"))
    b = asyncio.create_task(collect(client, "b", session=session))
    await eventually(lambda: len(gateway.sent) == 2)
    gateway.finish("a")
    gateway.finish("b")
    await asyncio.gather(a, b)


async def test_disconnect_does_not_release_slot_and_token_client_waits_for_observer():
    gateway = Gateway()
    client = gateway.client()
    client.disconnect = AsyncMock()
    first = asyncio.create_task(collect(client, "a"))
    await eventually(lambda: gateway.sent)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    retirement = asyncio.create_task(chat_serialization.retire_client(client))
    second = asyncio.create_task(collect(gateway.client(), "b"))
    await asyncio.sleep(.02)
    assert gateway.sent == ["a"]
    client.disconnect.assert_not_awaited()
    gateway.finish("a")
    await eventually(lambda: gateway.sent == ["a", "b"])
    await retirement
    client.disconnect.assert_awaited_once()
    gateway.finish("b")
    await second


async def test_cancel_queued_request_never_sends_it():
    gateway = Gateway()
    client = gateway.client()
    first = asyncio.create_task(collect(client, "a"))
    await eventually(lambda: gateway.sent)
    second = asyncio.create_task(collect(client, "b"))
    await asyncio.sleep(.01)
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    gateway.finish("a")
    await first
    assert gateway.sent == ["a"]
    assert not chat_serialization._slots[asyncio.get_running_loop()]


async def test_external_native_queue_empty_final_is_not_success_or_foreign_run_adoption():
    gateway = Gateway()
    gateway.started = False
    client = gateway.client()
    first = asyncio.create_task(collect(client, "queued"))
    await eventually(lambda: gateway.sent)
    gateway.final("queued")
    gateway.complete["queued"] = "ok"
    gateway.event("agent", {"runId": "unrelated", "sessionKey": "sk", "stream": "assistant",
                            "data": {"text": "must not attach this reply"}})
    result = await asyncio.wait_for(first, 2)
    assert result[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"
    assert not any(e.get("state") == "final" for e in result)
    assert (await collect(client, "next"))[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"
    assert gateway.sent == ["queued"]


@pytest.mark.parametrize("failure", [ConnectionError(), TimeoutError()])
async def test_completion_query_failure_fails_closed_without_automatic_resend(failure):
    gateway = Gateway()
    gateway.wait_failure = failure
    client = gateway.client()
    task = asyncio.create_task(collect(client, "a", timeout_ms=30))
    await eventually(lambda: gateway.sent)
    gateway.finish("a")
    assert (await task)[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"
    assert (await collect(client, "b"))[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"
    assert gateway.sent == ["a"]


async def test_downstream_ack_failure_does_not_cancel_dispatch_monitor():
    gateway = Gateway()
    client = gateway.client()
    ack = AsyncMock(side_effect=ConnectionError("downstream gone"))
    assert await collect(client, "a", on_ack=ack) == []
    second = asyncio.create_task(collect(client, "b"))
    await asyncio.sleep(.01)
    assert gateway.sent == ["a"]
    gateway.finish("a")
    await eventually(lambda: len(gateway.sent) == 2)
    gateway.finish("b")
    await second


@pytest.mark.parametrize("status", ["error", "timeout"])
async def test_confirmed_failure_releases_slot(status):
    gateway = Gateway()
    client = gateway.client()
    task = asyncio.create_task(collect(client, "a"))
    await eventually(lambda: gateway.sent)
    gateway.finish("a", status=status)
    assert (await task)[-1]["state"] == "error"
    next_task = asyncio.create_task(collect(client, "b"))
    await eventually(lambda: len(gateway.sent) == 2)
    gateway.finish("b")
    await next_task


async def test_command_reply_without_agent_lifecycle_is_supported():
    gateway = Gateway()
    gateway.started = False
    task = asyncio.create_task(collect(gateway.client(), "a", message="/status"))
    await eventually(lambda: gateway.sent)
    gateway.final("a", "status report")
    gateway.complete["a"] = "ok"
    assert (await task)[-1]["state"] == "final"


@pytest.mark.parametrize("version,methods,supported", [
    ("2026.5.12", ["agent.wait"], True), ("2026.5.12-1", ["agent.wait"], True),
    ("2026.5.12", [], False), ("2026.5.11", ["agent.wait"], False),
    ("2026.6.1", ["agent.wait"], False), ("unknown", ["agent.wait"], False),
])
def test_completion_contract_is_explicitly_version_gated(version, methods, supported):
    client = Gateway().client()
    client._hello.server.version = version
    client._hello.features.methods = methods
    assert supports_dispatch_wait(client) is supported


async def test_busy_gateway_probe_timeout_is_retried_without_resending_chat():
    gateway = Gateway()
    gateway.wait_failure = TimeoutError()
    task = asyncio.create_task(collect(gateway.client(), "a"))
    await eventually(lambda: gateway.sent)
    gateway.final("a", "before compaction")
    await eventually(lambda: len(gateway.polled) >= 2)
    assert not task.done()
    gateway.wait_failure = None
    gateway.finish("a", "after compaction")
    assert (await task)[-1]["message"]["content"][0]["text"] == "after compaction"
    assert gateway.sent == ["a"]


async def test_pending_child_still_holds_the_session_after_root_dispatch_completion():
    gateway = Gateway()
    client = gateway.client()
    first = asyncio.create_task(collect(client, "a"))
    await eventually(lambda: gateway.sent)
    child_session = "agent:main:subagent:child"
    gateway.event("agent", {"runId": "a", "sessionKey": "sk", "stream": "tool", "data": {
        "name": "sessions_spawn", "phase": "result", "result": {"details": {
            "status": "accepted", "childSessionKey": child_session, "runId": "child",
        }},
    }})
    gateway.finish("a")
    second = asyncio.create_task(collect(client, "b"))
    await eventually(lambda: gateway.polled)
    await asyncio.sleep(.01)
    assert gateway.sent == ["a"]
    assert not first.done()
    gateway.event("chat", {"runId": "child", "sessionKey": child_session, "state": "final"})
    gateway.event("chat", {"runId": f"announce:v1:{child_session}:child", "sessionKey": "sk",
                           "state": "final", "text": "child answer"})
    assert (await first)[-1]["text"] == "child answer"
    await eventually(lambda: len(gateway.sent) == 2)
    gateway.finish("b")
    await second


async def test_cancel_command_is_not_queued_behind_active_turn():
    gateway = Gateway()
    client = gateway.client()
    first = asyncio.create_task(collect(client, "a"))
    await eventually(lambda: gateway.sent)
    stop = asyncio.create_task(collect(client, "stop", message="/stop"))
    await eventually(lambda: len(gateway.sent) == 2)
    gateway.final("stop", "stopped")
    assert (await stop)[-1]["state"] == "final"
    gateway.finish("a", status="error")
    await first


async def test_queue_timeout_is_not_sent_and_does_not_poison_session():
    gateway = Gateway()
    client = gateway.client()
    first = asyncio.create_task(collect(client, "a"))
    await eventually(lambda: gateway.sent)
    assert (await collect(client, "b", timeout_ms=10))[-1]["errorCode"] == "CHAT_QUEUE_TIMEOUT"
    assert gateway.sent == ["a"]
    gateway.finish("a")
    await first


@pytest.mark.parametrize("exception", [ConnectionError(), TimeoutError()])
async def test_ack_transport_failure_is_unknown_not_unsent(exception):
    gateway = Gateway()
    gateway.send_failure = exception
    client = gateway.client()
    result = await collect(client, "a")
    assert result[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"
    assert (await collect(client, "b"))[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"
    assert gateway.sent == ["a"]


@pytest.mark.parametrize("limit", ["MAX_SESSIONS", "MAX_WAITERS"])
async def test_queue_has_bounded_admission(monkeypatch, limit):
    monkeypatch.setattr(chat_serialization, limit, 0)
    gateway = Gateway()
    assert (await collect(gateway.client(), "a"))[-1]["errorCode"] == "CHAT_QUEUE_FULL"
    assert not gateway.sent


async def test_slow_consumer_drops_output_but_keeps_completion_observer(monkeypatch):
    monkeypatch.setattr(chat_serialization, "MAX_BUFFER_BYTES", 1)
    gateway = Gateway()
    client = gateway.client()
    assert (await collect(client, "a"))[-1]["errorCode"] == "CHAT_CONSUMER_SLOW"
    assert chat_serialization._workers
    gateway.finish("a")
    await eventually(lambda: not chat_serialization._workers)
    assert not any(client._event_listeners.values())


async def test_cached_ack_only_unlocks_if_same_adapter_previously_verified_it():
    gateway = Gateway()
    client = gateway.client()
    task = asyncio.create_task(collect(client, "a"))
    await eventually(lambda: gateway.sent)
    gateway.finish("a")
    await task
    send = client.send_request
    client.send_request = AsyncMock(return_value=ResponseFrame.ok_response("ack", {"runId": "a", "status": "ok"}))
    assert await collect(client, "a", on_ack=AsyncMock()) == []
    assert not chat_serialization._slots[asyncio.get_running_loop()]
    client.send_request = send
    task = asyncio.create_task(collect(client, "b"))
    await eventually(lambda: gateway.sent == ["a", "b"])
    gateway.finish("b")
    await task
    client.send_request = AsyncMock(return_value=ResponseFrame.ok_response("ack", {"runId": "unknown", "status": "ok"}))
    assert await collect(client, "unknown", on_ack=AsyncMock()) == []
    assert (await collect(client, "next"))[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"


@pytest.mark.parametrize("ended_at", [None, True, -1, float("nan")])
async def test_malformed_completion_timestamp_never_means_success(ended_at):
    gateway = Gateway()
    client = gateway.client()
    send = client.send_request
    async def malformed(method, params, **kwargs):
        if method == "agent.wait":
            return ResponseFrame.ok_response("wait", {"runId": "a", "status": "ok", "endedAt": ended_at})
        return await send(method, params, **kwargs)
    client.send_request = malformed
    task = asyncio.create_task(collect(client, "a"))
    await eventually(lambda: gateway.sent)
    gateway.finish("a")
    assert (await task)[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"


async def test_explicit_abort_cancels_queued_request_without_sending_it():
    gateway = Gateway()
    client = gateway.client()
    send = client.send_request
    async def abort(method, params, **kwargs):
        if method == "chat.abort":
            assert params == {"sessionKey": "sk", "runId": "b"}
            return ResponseFrame.ok_response("abort", {"aborted": False, "runIds": []})
        return await send(method, params, **kwargs)
    client.send_request = abort
    a = asyncio.create_task(collect(client, "a"))
    await eventually(lambda: gateway.sent)
    b = asyncio.create_task(collect(client, "b"))
    await eventually(lambda: len(chat_serialization._dispatches) == 2)
    result = await client.chat_abort("sk", "b")
    assert result == {"success": True, "payload": {"aborted": True, "runIds": ["b"]}}
    assert (await b)[-1]["state"] == "aborted"
    assert gateway.sent == ["a"]
    gateway.finish("a")
    await a


@pytest.mark.parametrize("identity", [{}, {"sessionKey": "sk"}, {"runId": "", "sessionKey": "sk"}])
async def test_anonymous_terminal_cannot_end_a_verified_dispatch(identity):
    gateway = Gateway()
    task = asyncio.create_task(collect(gateway.client(), "a"))
    await eventually(lambda: gateway.sent)
    gateway.event("chat", {**identity, "state": "final", "text": "unrelated"})
    await asyncio.sleep(.01)
    assert not task.done()
    gateway.finish("a")
    assert (await task)[-1]["message"]["content"][0]["text"] == "answer"


async def test_ack_delivery_timeout_retains_completion_observer(monkeypatch):
    monkeypatch.setattr(chat_serialization, "ACK_DELIVERY_TIMEOUT", .01)
    gateway = Gateway()
    client = gateway.client()
    async def stuck(response):
        await asyncio.Event().wait()
    assert await collect(client, "a", on_ack=stuck) == []
    assert chat_serialization._workers
    gateway.finish("a")
    await eventually(lambda: not chat_serialization._workers)


async def test_queued_admission_is_acknowledged_once_before_execution_and_survives_disconnect():
    gateway = Gateway()
    client = gateway.client()
    first = asyncio.create_task(collect(client, "a"))
    await eventually(lambda: gateway.sent == ["a"])
    ack = AsyncMock()
    second = asyncio.create_task(collect(client, "b", on_ack=ack))
    await eventually(lambda: ack.await_count == 1)
    assert ack.call_args.args[0].payload == {"runId": "b", "status": "queued", "accepted": True}
    assert gateway.sent == ["a"]
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    gateway.finish("a")
    await first
    await eventually(lambda: gateway.sent == ["a", "b"])
    gateway.finish("b")
    await eventually(lambda: not chat_serialization._workers)
    ack.assert_awaited_once()
    assert not chat_serialization._slots[asyncio.get_running_loop()]


@pytest.mark.parametrize("cached", [False, True])
async def test_rejected_or_cached_queued_request_reports_stream_error_without_second_ack(cached):
    gateway = Gateway()
    client = gateway.client()
    send = client.send_request

    async def reject(method, params, **kwargs):
        if method == "chat.send" and params["idempotencyKey"] == "b":
            if cached:
                return ResponseFrame.ok_response("b", {"runId": "b", "status": "ok"})
            return ResponseFrame.from_dict({"type": "res", "id": "b", "ok": False,
                                            "error": {"code": "DENIED", "message": "rejected"}})
        return await send(method, params, **kwargs)

    client.send_request = reject
    first = asyncio.create_task(collect(client, "a"))
    await eventually(lambda: gateway.sent == ["a"])
    ack = AsyncMock()
    second = asyncio.create_task(collect(client, "b", on_ack=ack))
    await eventually(lambda: ack.await_count == 1)
    gateway.finish("a")
    await first
    result = await second
    assert result[-1]["errorCode"] == ("CHAT_REQUEST_CACHED" if cached else "DENIED")
    assert not any(event.get("state") == "final" for event in result)
    ack.assert_awaited_once()


async def test_failed_queue_admission_never_sends_request_or_blocks_the_session():
    gateway = Gateway()
    client = gateway.client()
    first = asyncio.create_task(collect(client, "a"))
    await eventually(lambda: gateway.sent == ["a"])
    result = await collect(client, "b", on_ack=AsyncMock(side_effect=ConnectionError()))
    assert result[-1]["state"] == "error"
    gateway.finish("a")
    await first
    assert gateway.sent == ["a"]
    assert not chat_serialization._slots[asyncio.get_running_loop()]


async def test_compaction_retry_recovers_intermediate_error():
    gateway = Gateway()
    task = asyncio.create_task(collect(gateway.client(), "a"))
    await eventually(lambda: gateway.sent)
    gateway.agent("a", "lifecycle", phase="error")
    gateway.event("chat", {"runId": "a", "state": "error", "errorCode": "OVERFLOW"})
    await eventually(lambda: gateway.polled)
    assert not task.done()
    gateway.agent("a", "lifecycle", phase="start")
    gateway.agent("a", "assistant", text="recovered answer")
    gateway.complete["a"] = "ok"
    result = await task
    assert result[-1]["state"] == "final"
    assert result[-1]["message"]["content"][0]["text"] == "recovered answer"
    assert not any(event.get("state") == "error" for event in result)


@pytest.mark.parametrize("payload", [
    {"runId": "other", "status": "ok", "endedAt": 42},
    {"runId": "a", "status": "unexpected", "endedAt": 42},
])
async def test_uncorrelated_or_unknown_completion_cannot_release_session(payload):
    gateway = Gateway()
    client = gateway.client()
    send = client.send_request

    async def malformed(method, params, **kwargs):
        if method == "agent.wait":
            return ResponseFrame.ok_response("wait", payload)
        return await send(method, params, **kwargs)

    client.send_request = malformed
    task = asyncio.create_task(collect(client, "a"))
    await eventually(lambda: gateway.sent)
    gateway.finish("a")
    assert (await task)[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"
    assert (await collect(client, "b"))[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"
    assert gateway.sent == ["a"]


async def test_upstream_rejection_without_ack_callback_remains_a_stream_error():
    gateway = Gateway()
    client = gateway.client()
    client.send_request = AsyncMock(return_value=ResponseFrame.from_dict({
        "type": "res", "id": "a", "ok": False,
        "error": {"code": "DENIED", "message": "rejected"},
    }))
    result = await collect(client, "a")
    assert result[-1]["errorCode"] == "DENIED"
    assert result[-1]["errorMessage"] == "rejected"
    assert not chat_serialization._slots[asyncio.get_running_loop()]
