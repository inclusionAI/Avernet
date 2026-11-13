"""Streaming replay uses real chunk persistence and actual executor/consumer methods."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.repository.bot_run_queue_chunk import (
    OrmBotRunQueueChunkRepository,
)
from secbaas.community.core.service.bot_run._executor import BotRunRequestExecutor
from secbaas.community.core.service.bot_run._queue_task_message_dispatcher import (
    QueueTaskMessageDispatcher,
)
from secbaas.community.plugins.database.sqlite.sqlite_orm import SqliteOrmPlugin


@pytest.fixture
def chunks():
    db = SqliteOrmPlugin("sqlite:///:memory:")
    db.create_all()
    yield OrmBotRunQueueChunkRepository(db)


def completed(content="ab", at="first", short="short"):
    return SimpleNamespace(
        status="COMPLETED",
        result_content_long=content,
        result_content=short,
        completed_at=at,
    )


def dispatcher(chunks, run=None, watermark="0:delta"):
    runs = MagicMock()
    runs.get_by_run_id.return_value = run or completed()
    cache = MagicMock()
    cache.get.return_value = watermark
    config = MagicMock()
    config.get_config.return_value = SimpleNamespace(conf_value="false")
    return QueueTaskMessageDispatcher(
        runs, MagicMock(), chunks, cache, system_config_service=config
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 2, 120])
@pytest.mark.parametrize("cache_failure", [False, True])
async def test_completed_drains_all_rows_despite_short_watermark(
    chunks, count, cache_failure
):
    for seq in range(1, count + 1):
        chunks.insert_chunk(run_id="r", seq=seq, chunk_type="delta", content=str(seq))
    d = dispatcher(chunks, watermark="2:final")
    if cache_failure:
        d._cache_plugin.get.side_effect = RuntimeError("cache offline")
    output = [c async for c in d._poll_chunks("r", timeout=2)]
    assert [(c.type, c.content) for c in output] == [
        *(("delta", str(i)) for i in range(1, count + 1)),
        ("final", "ab"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "long,short,want", [("", "short", ""), (None, "short", "short"), (None, None, "")]
)
async def test_empty_and_legacy_completed_results(chunks, long, short, want):
    d = dispatcher(chunks, run=completed(long, short=short))
    output = [c async for c in d._poll_chunks("r", timeout=1)]
    assert [(c.type, c.content) for c in output] == [("final", want)]


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["final", "error", "aborted"])
async def test_stored_terminal_wins(chunks, terminal):
    chunks.insert_chunk(run_id="r", seq=1, chunk_type=terminal, content="stored")
    d = dispatcher(chunks)
    output = [c async for c in d._poll_chunks("r", timeout=1)]
    assert [(c.type, c.content) for c in output] == [(terminal, "stored")]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["FAILED", "TIME_OUT", "ABORTED"])
async def test_current_failure_overrides_saved_success(chunks, status):
    d = dispatcher(chunks)
    d._run_repository.get_by_run_id.side_effect = [
        completed("old"),
        SimpleNamespace(status=status),
    ]
    output = [c async for c in d._poll_chunks("r", timeout=1)]
    assert len(output) == 1
    assert output[0].type == ("aborted" if status == "ABORTED" else "error")
    assert status in output[0].content


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["PENDING", "RUNNING", None])
async def test_reopened_or_missing_run_invalidates_completion(chunks, status):
    d = dispatcher(chunks)
    current = SimpleNamespace(status=status) if status else None
    d._run_repository.get_by_run_id.side_effect = [completed("old"), current, current]
    output = [c async for c in d._poll_chunks("r", timeout=0.3)]
    assert [(c.type, c.content) for c in output] == [("error", "stream timeout")]


@pytest.mark.asyncio
async def test_replacement_completion_redrains_new_rows(chunks):
    d = dispatcher(chunks)
    calls = 0

    def read(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            return completed("old")
        if calls == 2:
            chunks.insert_chunk(
                run_id="r", seq=1, chunk_type="delta", content="new row"
            )
        return completed("new", at="second")

    d._run_repository.get_by_run_id.side_effect = read
    output = [c async for c in d._poll_chunks("r", timeout=1)]
    assert [(c.type, c.content) for c in output] == [
        ("delta", "new row"),
        ("final", "new"),
    ]
    assert calls >= 3


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["chunk_read", "initial_status", "revalidate"])
async def test_database_read_errors_do_not_invent_success(chunks, phase):
    d = dispatcher(chunks)
    error = RuntimeError("database unavailable")
    if phase == "chunk_read":
        d._chunk_repository = MagicMock()
        d._chunk_repository.get_chunks_after.side_effect = error
    else:
        d._run_repository.get_by_run_id.side_effect = (
            [error] if phase == "initial_status" else [completed(), error]
        )
    with pytest.raises(RuntimeError, match="database unavailable"):
        _ = [c async for c in d._poll_chunks("r", timeout=1)]


@pytest.mark.asyncio
async def test_cancelled_poll_preserves_cleanup(chunks):
    d = dispatcher(chunks, run=SimpleNamespace(status="RUNNING"))
    d._system_config_service = None
    d._chunk_repository = MagicMock(wraps=chunks)
    task = asyncio.create_task(anext(d._poll_chunks("r")))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    d._chunk_repository.delete_chunks_by_run.assert_called_once_with("r")


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", [1, 2, 120])
async def test_actual_executor_replay_completes_and_consumer_terminates(chunks, prefix):
    for seq in range(1, prefix + 1):
        chunks.insert_chunk(
            run_id="r", seq=seq, chunk_type="delta", content="a" if seq == 1 else "b"
        )
    first = chunks.get_chunks_after("r", 0)[0]
    d = dispatcher(chunks)
    state = SimpleNamespace(
        status="RUNNING",
        result_content_long=None,
        result_content=None,
        completed_at=None,
    )
    d._run_repository.get_by_run_id.return_value = state

    def save(**kwargs):
        state.status = "COMPLETED"
        state.result_content_long = kwargs["content_long"]
        state.completed_at = "saved"

    d._run_repository.update_result.side_effect = save
    executor = BotRunRequestExecutor.__new__(BotRunRequestExecutor)
    executor._repo = d._run_repository
    executor._chunk_repository = chunks
    executor._cache_plugin = d._cache_plugin
    executor._stream_flush_interval = 0.2
    executor._stream_flush_max_content_bytes = 10000

    async def stream(**kwargs):
        yield StreamChunk(type="delta", content="ab")
        yield StreamChunk(type="final", content="ab")

    service = SimpleNamespace(send_message_stream=stream)
    await executor._do_send_stream(
        SimpleNamespace(run_id="r", message_long="input"),
        service,
        "session",
        2,
        MagicMock(),
        MagicMock(),
    )
    d._run_repository.update_result.assert_called_once()
    d._run_repository.update_error.assert_not_called()
    assert chunks.get_chunks_after("r", 0)[0] == first
    d._cache_plugin.get.return_value = "2:final"
    output = [c async for c in d._poll_chunks("r", timeout=2)]
    assert output[-1].type == "final" and output[-1].content == "ab"
    assert len(output) == (2 if prefix == 1 else prefix + 1)
