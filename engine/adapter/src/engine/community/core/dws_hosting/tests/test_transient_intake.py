"""History service faults must not tear down the live event consumer."""

import asyncio

import pytest

from engine.community.kernel.dws_connection import DwsError, ListenerNotice

from .test_intake import setup as intake_setup, wait_until
from .test_runtime import stop

setup = intake_setup


async def test_history_retries_same_conversation_without_restarting_listener(setup):
    state, _, client, intake = setup
    original = client.history
    attempts = []

    async def history(cid, start, end):
        attempts.append((cid, start, end))
        if len(attempts) < 3:
            raise DwsError("command_failed")
        return await original(cid, start, end)

    client.history = history
    task = asyncio.create_task(intake.run())
    try:
        await wait_until(lambda: len(client.calls) > 0)
        await wait_until(lambda: intake._recovered)
        assert attempts[:3] == [("chat", 1000, 5000)] * 3
        assert client.listener_attempts == 1
        assert state.claim_ready("binding", now_ms=10000) is not None
    finally:
        await stop(task)


async def test_history_outage_keeps_hints_and_claims_closed_until_recovery(setup):
    state, recovery, client, intake = setup
    client.error = DwsError("incomplete_discovery")
    task = asyncio.create_task(intake.run())
    try:
        await wait_until(lambda: client.listener_attempts == 1)
        await client.notices.put(
            ListenerNotice(
                kind="conversation",
                conversation_id="chat",
                message_id="m1",
                created_at_ms=1100,
            )
        )
        await wait_until(lambda: bool(recovery.known_conversations("binding")))
        await asyncio.sleep(0.07)
        assert client.listener_attempts == 1
        assert not client.listener_closed
        assert state.claim_ready("binding", now_ms=10000) is None
        client.error = None
        await wait_until(lambda: intake._recovered)
        assert state.claim_ready("binding", now_ms=10000) is not None
    finally:
        await stop(task)


@pytest.mark.parametrize("code", ["command_failed", "identity_mismatch"])
async def test_reconcile_fails_closed_immediately_on_upstream_error(setup, code):
    state, _, client, intake = setup
    assert await intake.reconcile_once()
    intake.clock_ms = lambda: 6000
    calls = []

    async def fail(*args):
        calls.append(args)
        raise DwsError(code)

    client.history = fail
    with pytest.raises(DwsError, match=code):
        await intake.reconcile_once()
    assert len(calls) == 1
    assert state.claim_ready("binding", now_ms=10000) is None


async def test_non_transient_recovery_failure_stops_and_closes_consumer(setup):
    _, _, client, intake = setup
    client.error = DwsError("identity_mismatch")
    with pytest.raises(DwsError, match="identity_mismatch"):
        await asyncio.wait_for(intake.run(), 1)
    assert client.listener_closed == 1
    assert not client.locked
