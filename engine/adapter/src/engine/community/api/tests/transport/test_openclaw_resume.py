from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from engine.community.api.transport import openclaw_resume
from engine.community.api.transport.openclaw_resume import OpenClawResumeRegistry


@pytest.mark.asyncio
async def test_cumulative_snapshots_compress_without_changing_wire_frames(monkeypatch):
    monkeypatch.setattr(openclaw_resume, "MAX_BYTES_PER_RUN", 16_000)
    registry = OpenClawResumeRegistry()
    run, _ = registry.create(session_key="sk", run_id="r")
    live = AsyncMock()
    await run.attach("live", live)
    for index in range(50):
        await run.publish("chat", {"runId": "r", "state": "delta", "text": "story " * (200 + index)})
        await asyncio.sleep(0)
    await run.publish("chat", {"runId": "r", "state": "final"})
    await run.finish()
    await run._subscribers["live"].task
    assert run.resumable
    assert run.buffered_bytes < 16_000 < run._replay_bytes
    replay = AsyncMock()
    await run.attach("replay", replay)
    await run._subscribers["replay"].task
    assert replay.send_text.await_args_list == live.send_text.await_args_list


@pytest.mark.asyncio
async def test_uncompressed_replay_budget_still_bounded(monkeypatch):
    monkeypatch.setattr(openclaw_resume, "MAX_REPLAY_BYTES_PER_RUN", 2000)
    registry = OpenClawResumeRegistry()
    run, _ = registry.create(session_key="sk", run_id="r")
    await run.publish("chat", {"text": "x" * 3000})
    assert run.reason == "CACHE_LIMIT"
    assert registry.buffered_bytes == run.buffered_bytes == 0


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
        "startedAt": run.started_at_ms,
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


@pytest.mark.asyncio
async def test_small_queue_and_cache_overflow_preserve_live_final(monkeypatch):
    monkeypatch.setattr(openclaw_resume, "SUBSCRIBER_QUEUE_SIZE", 1)
    monkeypatch.setattr(openclaw_resume, "MAX_EVENTS_PER_RUN", 1)
    registry = OpenClawResumeRegistry()
    run, _ = registry.create(session_key="sk", run_id="run")
    socket = AsyncMock()
    await run.attach("live", socket)
    sender = run._subscribers["live"].task
    for index in range(5):
        await run.publish("chat", {"state": "delta", "text": str(index)})
    await run.publish("chat", {"state": "final", "text": "done"})
    await run.finish()
    await asyncio.wait_for(sender, 1)
    frames = [json.loads(c.args[0])["payload"] for c in socket.send_text.await_args_list]
    assert [f["text"] for f in frames] == ["0", "1", "2", "3", "4", "done"]
    assert run.reason == "CACHE_LIMIT"
    assert run.state == "final"
    socket.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_stalled_subscriber_closes_but_run_remains_recoverable(monkeypatch):
    monkeypatch.setattr(openclaw_resume, "SUBSCRIBER_QUEUE_SIZE", 1)
    monkeypatch.setattr(openclaw_resume, "SUBSCRIBER_QUEUE_TIMEOUT_SECONDS", 0.01)
    registry = OpenClawResumeRegistry()
    run, ticket = registry.create(session_key="sk", run_id="run")
    blocked = asyncio.Event()
    async def send(_frame):
        await blocked.wait()
    slow, healthy = AsyncMock(), AsyncMock()
    slow.send_text.side_effect = send
    await run.attach("slow", slow)
    slow_task = run._subscribers["slow"].task
    await run.attach("healthy", healthy)
    healthy_task = run._subscribers["healthy"].task
    for index in range(4):
        await run.publish("chat", {"state": "delta", "text": str(index)})
    await run.publish("chat", {"state": "final"})
    await run.finish()
    await asyncio.wait_for(healthy_task, 1)
    await asyncio.gather(slow_task, return_exceptions=True)
    slow.close.assert_awaited_once_with(code=1013)
    healthy.close.assert_not_awaited()
    assert healthy.send_text.await_count == 5
    assert registry.lookup(session_key="sk", ticket=ticket) is run
    assert run.resumable and len(run._events) == 5


@pytest.mark.asyncio
async def test_failed_subscriber_write_closes_socket():
    registry = OpenClawResumeRegistry()
    run, _ = registry.create(session_key="sk", run_id="run")
    socket = AsyncMock()
    socket.send_text.side_effect = RuntimeError("broken transport")
    await run.attach("broken", socket)
    sender = run._subscribers["broken"].task
    await run.publish("chat", {"state": "delta"})
    await asyncio.wait_for(sender, 1)
    socket.close.assert_awaited_once_with(code=1011)
    assert not run._subscribers
