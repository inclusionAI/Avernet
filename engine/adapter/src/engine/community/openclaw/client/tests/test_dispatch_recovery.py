from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from engine.community.openclaw.client import chat_serialization
from engine.community.openclaw.client.tests.test_chat_completion import (
    Gateway, cleanup, collect, eventually,
)
from engine.community.openclaw.protocol import ResponseFrame

pytestmark = pytest.mark.usefixtures("cleanup")


async def block(gateway, client, **kwargs):
    task = asyncio.create_task(collect(client, "old", timeout_ms=30, **kwargs))
    await eventually(lambda: gateway.sent == ["old"])
    assert (await task)[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"
    return chat_serialization._slots[asyncio.get_running_loop()][
        chat_serialization._session_key(client, "sk")
    ]


@pytest.mark.parametrize("status", ["ok", "error", "timeout"])
async def test_late_completion_recovers_on_new_client_without_replaying_old_task(status):
    gateway = Gateway()
    old_client = gateway.client()
    slot = await block(gateway, old_client)
    assert slot.blocked and slot.unconfirmed.run == "old"
    assert not any(old_client._event_listeners.values())
    gateway.complete["old"] = status
    task = asyncio.create_task(collect(gateway.client(), "new", session="agent:main:sk"))
    await eventually(lambda: gateway.sent == ["old", "new"])
    assert gateway.polled == ["old"]
    assert slot.unconfirmed is None
    gateway.finish("new")
    result = await task
    assert [e["runId"] for e in result if e.get("state") == "final"] == ["new"]
    assert not chat_serialization._slots[asyncio.get_running_loop()]


async def test_active_dispatch_stays_blocked_and_failed_probes_are_rate_limited():
    gateway = Gateway()
    client = gateway.client()
    slot = await block(gateway, client)
    for run in ("new1", "new2"):
        assert (await collect(client, run))[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"
    assert gateway.sent == ["old"]
    assert gateway.polled == ["old"]
    assert slot.unconfirmed.run == "old"
    gateway.complete["old"] = "ok"
    slot.next_probe_at = 0
    task = asyncio.create_task(collect(client, "new3"))
    await eventually(lambda: gateway.sent == ["old", "new3"])
    gateway.finish("new3")
    await task


@pytest.mark.parametrize("payload", [
    {"runId": "other", "status": "ok", "endedAt": 42},
    {"runId": "old", "status": "started", "endedAt": 42},
    *({"runId": "old", "status": "ok", "endedAt": value}
      for value in (None, True, False, 0, -1, "42", float("nan"), float("inf"))),
])
async def test_invalid_or_unrelated_recovery_never_releases_the_slot(payload):
    gateway = Gateway()
    client = gateway.client()
    slot = await block(gateway, client)
    client.send_request = AsyncMock(return_value=ResponseFrame.ok_response("probe", payload))
    assert (await collect(client, "new"))[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"
    assert gateway.sent == ["old"]
    assert slot.blocked and slot.unconfirmed.run == "old"
    assert client.send_request.call_args.args == ("agent.wait", {"runId": "old", "timeoutMs": 0})


@pytest.mark.parametrize("failure", [TimeoutError(), ConnectionError(), RuntimeError()])
async def test_recovery_rpc_failure_preserves_original_identity(failure):
    gateway = Gateway()
    client = gateway.client()
    slot = await block(gateway, client)
    gateway.wait_failure = failure
    assert (await collect(client, "new"))[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"
    assert slot.unconfirmed.run == "old"
    assert gateway.sent == ["old"]


async def test_rejected_recovery_probe_preserves_the_block():
    gateway = Gateway()
    client = gateway.client()
    slot = await block(gateway, client)
    client.send_request = AsyncMock(return_value=ResponseFrame.from_dict({
        "type": "res", "id": "probe", "ok": False,
        "error": {"code": "UNAVAILABLE", "message": "not available"},
    }))
    assert (await collect(client, "new"))[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"
    assert slot.unconfirmed.run == "old"


async def test_recovery_has_a_deadline_even_if_client_ignores_rpc_timeout(monkeypatch):
    monkeypatch.setattr(chat_serialization, "QUERY_TIMEOUT", .02)
    gateway = Gateway()
    client = gateway.client()
    slot = await block(gateway, client)
    async def stuck(*args, **kwargs):
        await asyncio.Event().wait()
    client.send_request = stuck
    result = await asyncio.wait_for(collect(client, "new"), .5)
    assert result[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"
    assert slot.unconfirmed.run == "old"


async def test_concurrent_recovery_keeps_new_dispatches_serialized():
    gateway = Gateway()
    await block(gateway, gateway.client())
    gateway.complete["old"] = "ok"
    a = asyncio.create_task(collect(gateway.client(), "a"))
    b = asyncio.create_task(collect(gateway.client(), "b"))
    await eventually(lambda: gateway.sent == ["old", "a"])
    await asyncio.sleep(.01)
    assert gateway.sent == ["old", "a"]
    assert gateway.polled == ["old"]
    gateway.finish("a")
    await a
    await eventually(lambda: gateway.sent == ["old", "a", "b"])
    gateway.finish("b")
    await b


async def test_cancel_during_recovery_preserves_block_and_never_sends():
    gateway = Gateway()
    client = gateway.client()
    slot = await block(gateway, client)
    probing = asyncio.Event()
    async def stuck(*args, **kwargs):
        probing.set()
        await asyncio.Event().wait()
    client.send_request = stuck
    task = asyncio.create_task(collect(client, "new"))
    await probing.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not slot.lock.locked()
    assert slot.unconfirmed.run == "old"
    assert gateway.sent == ["old"]


@pytest.mark.parametrize("child_completed", [False, True])
async def test_root_completion_cannot_recover_unsettled_children_or_announces(child_completed):
    gateway = Gateway()
    client = gateway.client()
    task = asyncio.create_task(collect(client, "old", timeout_ms=30))
    await eventually(lambda: gateway.sent)
    child_session = "agent:main:subagent:child"
    gateway.agent("old", "tool", name="sessions_spawn", phase="result", result={"details": {
        "status": "accepted", "childSessionKey": child_session, "runId": "child",
    }})
    if child_completed:
        gateway.event("chat", {"runId": "child", "sessionKey": child_session, "state": "final"})
    assert (await task)[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"
    gateway.complete["old"] = "ok"
    assert (await collect(client, "new"))[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"
    assert gateway.sent == ["old"]
    assert not gateway.polled


async def test_unobserved_execution_cannot_recover_from_cached_ok():
    gateway = Gateway()
    gateway.started = False
    client = gateway.client()
    await block(gateway, client)
    gateway.complete["old"] = "ok"
    assert (await collect(client, "new"))[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"
    assert gateway.sent == ["old"]


async def test_non_generating_command_can_recover_with_observed_answer():
    gateway = Gateway()
    gateway.started = False
    client = gateway.client()
    task = asyncio.create_task(collect(client, "old", message="/status", timeout_ms=30))
    await eventually(lambda: gateway.sent)
    gateway.final("old", "status report")
    assert (await task)[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"
    gateway.complete["old"] = "ok"
    gateway.started = True
    task = asyncio.create_task(collect(client, "new"))
    await eventually(lambda: gateway.sent == ["old", "new"])
    gateway.finish("new")
    assert (await task)[-1]["runId"] == "new"


async def test_second_unconfirmed_dispatch_replaces_recovered_run_metadata():
    gateway = Gateway()
    client = gateway.client()
    slot = await block(gateway, client)
    gateway.complete["old"] = "ok"
    assert (await collect(client, "new", timeout_ms=30))[-1]["errorCode"] == "DISPATCH_UNCONFIRMED"
    assert slot.unconfirmed.run == "new"
    assert gateway.sent == ["old", "new"]


async def test_explicit_cached_retry_after_recovery_does_not_block_again():
    gateway = Gateway()
    client = gateway.client()
    await block(gateway, client)
    gateway.complete["old"] = "ok"
    send = client.send_request
    async def cached(method, params, **kwargs):
        if method == "chat.send":
            assert params["idempotencyKey"] == "old"
            return ResponseFrame.ok_response("ack", {"runId": "old", "status": "ok"})
        return await send(method, params, **kwargs)
    client.send_request = cached
    ack = AsyncMock()
    assert await collect(client, "old", on_ack=ack) == []
    ack.assert_awaited_once()
    assert not chat_serialization._slots[asyncio.get_running_loop()]
    assert gateway.sent == ["old"]


async def test_background_recovery_survives_consumer_and_preserves_proof_after_cache_expiry(monkeypatch):
    monkeypatch.setattr(chat_serialization, "RECOVERY_PROBE_INTERVAL", .01)
    gateway = Gateway()
    client = gateway.client()
    client.disconnect = AsyncMock()
    await block(gateway, client)
    assert chat_serialization._workers
    retirement = asyncio.create_task(chat_serialization.retire_client(client))
    await asyncio.sleep(.02)
    client.disconnect.assert_not_awaited()
    gateway.complete["old"] = "ok"
    await asyncio.wait_for(retirement, 1)
    client.disconnect.assert_awaited_once()
    assert not chat_serialization._workers
    assert not chat_serialization._slots[asyncio.get_running_loop()]
    gateway.complete.clear()
    chat_serialization._completed.clear()
    task = asyncio.create_task(collect(gateway.client(), "new"))
    await eventually(lambda: gateway.sent == ["old", "new"])
    gateway.finish("new")
    assert (await task)[-1]["runId"] == "new"


async def test_background_recovery_is_bounded_and_keeps_unknown_execution_blocked(monkeypatch):
    monkeypatch.setattr(chat_serialization, "RECOVERY_PROBE_INTERVAL", .005)
    monkeypatch.setattr(chat_serialization, "RECOVERY_WINDOW_SECONDS", .02)
    gateway = Gateway()
    client = gateway.client()
    slot = await block(gateway, client)
    await eventually(lambda: not chat_serialization._workers)
    assert slot.blocked and slot.unconfirmed.run == "old"
    assert gateway.polled
    assert gateway.sent == ["old"]


async def test_background_recovery_does_not_touch_a_replacement_dispatch(monkeypatch):
    monkeypatch.setattr(chat_serialization, "RECOVERY_PROBE_INTERVAL", .01)
    gateway = Gateway()
    client = gateway.client()
    slot = await block(gateway, client)
    gateway.complete["old"] = "ok"
    task = asyncio.create_task(collect(client, "new"))
    await eventually(lambda: gateway.sent == ["old", "new"])
    await eventually(lambda: len(chat_serialization._workers) == 1)
    assert slot.lock.locked()
    assert not task.done()
    gateway.finish("new")
    await task
