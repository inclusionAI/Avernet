"""Reception timestamps and source diagnostics must not expose message bodies."""

import asyncio
import logging
import sqlite3

import pytest

from .test_intake import setup as intake_setup, wait_until
from .test_recovery_contract import hint

setup = intake_setup


async def test_slow_history_uses_actual_arrival_not_scan_start(setup, caplog):
    state, recovery, client, intake = setup
    caplog.set_level(logging.INFO)
    client.after_history = lambda: setattr(intake, "clock_ms", lambda: 34000)
    assert await intake.reconcile_once()
    with sqlite3.connect(recovery._database.path) as db:
        assert db.execute("SELECT received_ms FROM inbox").fetchone()[0] == 34000
    # Four-second batching begins at receipt, not the earlier scan start.
    assert state.claim_ready("binding", now_ms=34000) is None
    assert state.claim_ready("binding", now_ms=39000) is not None
    assert "source=history_scan" in caplog.text
    assert f"delay_ms={34000 - client.result.messages[0].created_at_ms}" in caplog.text
    assert client.result.messages[0].text not in caplog.text


async def test_live_event_and_history_share_private_correlation_key(setup, caplog):
    _, _, client, intake = setup
    caplog.set_level(logging.INFO)
    collector = asyncio.create_task(intake._collect())
    try:
        await client.notices.put(hint())
        await wait_until(lambda: intake._wake.is_set())
        await intake.hints_once()
    finally:
        collector.cancel()
        with pytest.raises(asyncio.CancelledError):
            await collector
    records = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("DWS intake:")
    ]
    assert len(records) == 2
    assert "source=live_event" in records[0]
    assert "source=live_history" in records[1]
    assert (
        records[0].split("message_key=")[1].split()[0]
        == records[1].split("message_key=")[1].split()[0]
    )
    assert client.result.messages[0].text not in caplog.text
    assert "conversation_id=" not in caplog.text


async def test_duplicate_history_is_not_logged_as_a_new_arrival(setup, caplog):
    _, _, _, intake = setup
    caplog.set_level(logging.INFO)
    await intake.reconcile_once()
    caplog.clear()
    intake.clock_ms = lambda: 6000
    await intake.reconcile_once()
    assert "DWS intake:" not in caplog.text
