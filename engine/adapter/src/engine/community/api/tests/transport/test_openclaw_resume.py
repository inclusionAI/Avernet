from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from engine.community.api.transport import openclaw_resume
from engine.community.api.transport.openclaw_resume import OpenClawResumeRegistry


def test_ticket_is_required_and_not_retained_in_plaintext() -> None:
    registry = OpenClawResumeRegistry()
    created = registry.create(session_key="session:one", run_id="run-1")
    assert created is not None
    run, ticket = created

    assert ticket not in repr(registry._runs)
    assert registry.lookup(session_key="session:one", ticket=ticket) is run
    assert registry.lookup(session_key="session:other", ticket=ticket) is None
    assert registry.lookup(session_key="session:one", ticket="wrong-ticket") is None
    assert registry.lookup(session_key="session:one", ticket=None) is None


@pytest.mark.asyncio
async def test_resume_replays_before_live_events_after_disconnect() -> None:
    registry = OpenClawResumeRegistry()
    created = registry.create(session_key="session:one", run_id="run-1")
    assert created is not None
    run, ticket = created

    first = AsyncMock()
    await run.attach("conn-old", first)
    await run.publish("agent", {"seq": 11, "state": "delta", "text": "Hello"})
    await asyncio.sleep(0)
    await run.detach("conn-old")
    await run.publish("agent", {"seq": 12, "state": "delta", "text": " world"})

    second = AsyncMock()
    restored = registry.lookup(session_key="session:one", ticket=ticket)
    assert restored is run
    boundary = await restored.attach("conn-new", second)
    assert boundary == {
        "runId": "run-1",
        "state": "starting",
        "replayCount": 2,
        "lastResumeSeq": 2,
    }
    await run.publish("chat", {"seq": 13, "state": "final", "text": "Hello world"})
    await run.finish()
    await run._subscribers["conn-new"].task

    frames = [json.loads(call.args[0]) for call in second.send_text.await_args_list]
    assert [frame["payload"]["resumeSeq"] for frame in frames] == [1, 2, 3]
    assert [frame["payload"]["seq"] for frame in frames] == [11, 12, 13]
    assert frames[-1]["payload"]["state"] == "final"


@pytest.mark.asyncio
async def test_injected_final_does_not_finish_foreground_run() -> None:
    registry = OpenClawResumeRegistry()
    created = registry.create(session_key="session:one", run_id="run-1")
    assert created is not None
    run, _ = created

    await run.publish("chat", {"seq": 1, "runId": "inject-1", "state": "final"})
    assert run.state == "starting"
    await run.publish("chat", {"seq": 2, "runId": "run-1", "state": "final"})
    assert run.state == "final"


@pytest.mark.asyncio
async def test_terminal_run_can_be_replayed_after_it_finishes() -> None:
    registry = OpenClawResumeRegistry()
    created = registry.create(session_key="session:one", run_id="run-1")
    assert created is not None
    run, ticket = created
    await run.publish("agent", {"seq": 1, "runId": "run-1", "state": "delta"})
    await run.publish("chat", {"seq": 2, "runId": "run-1", "state": "final"})
    await run.finish()

    socket = AsyncMock()
    restored = registry.lookup(session_key="session:one", ticket=ticket)
    assert restored is run
    result = await restored.attach("conn-new", socket)
    assert result is not None and result["state"] == "final"
    await run._subscribers["conn-new"].task
    frames = [json.loads(call.args[0]) for call in socket.send_text.await_args_list]
    assert [frame["payload"]["resumeSeq"] for frame in frames] == [1, 2]


@pytest.mark.asyncio
async def test_capacity_overflow_disables_replay_without_truncating_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(openclaw_resume, "MAX_BYTES_PER_RUN", 200)
    registry = OpenClawResumeRegistry()
    created = registry.create(session_key="session:one", run_id="run-1")
    assert created is not None
    run, _ = created

    await run.publish("agent", {"seq": 1, "text": "x" * 500})
    assert run.reason == "CACHE_LIMIT"
    assert run.buffered_bytes == 0
    assert registry.buffered_bytes == 0
    assert await run.attach("conn-new", AsyncMock()) is None


@pytest.mark.asyncio
async def test_run_limit_eviction_prefers_completed_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(openclaw_resume, "MAX_RUNS", 2)
    registry = OpenClawResumeRegistry()
    first = registry.create(session_key="session:first", run_id="run-1")
    second = registry.create(session_key="session:second", run_id="run-2")
    assert first is not None and second is not None
    first_run, first_ticket = first
    _, second_ticket = second
    assert registry.create(session_key="session:third", run_id="run-3") is None

    await first_run.publish("chat", {"state": "final"})
    await first_run.finish()
    third = registry.create(session_key="session:third", run_id="run-3")
    assert third is not None
    assert registry.lookup(session_key="session:first", ticket=first_ticket) is None
    assert registry.lookup(session_key="session:second", ticket=second_ticket) is not None


@pytest.mark.asyncio
async def test_terminal_run_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openclaw_resume, "TERMINAL_TTL_SECONDS", 0)
    registry = OpenClawResumeRegistry()
    created = registry.create(session_key="session:one", run_id="run-1")
    assert created is not None
    run, ticket = created
    await run.publish("chat", {"seq": 1, "state": "final"})
    await run.finish()

    assert registry.lookup(session_key="session:one", ticket=ticket) is None
    assert registry.buffered_bytes == 0
