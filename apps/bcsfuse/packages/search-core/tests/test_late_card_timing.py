"""A timed-out caller must not hide the synchronous provider's final timings."""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from search_core.core.execution import DeadlineExecutor
from search_core.core.observation import events, request_budget
from search_core.core.timing import request_timing, stage_timing


@pytest.mark.parametrize("cancel,fail", [(False, False), (False, True), (True, False)])
def test_worker_progress_survives_request_exit(caplog, cancel, fail):
    events.flush(force=True)
    caplog.clear()
    caplog.set_level(logging.INFO, logger="search_core.events")
    started, release = Event(), Event()

    def load():
        with stage_timing("card_source", emit_progress=True) as source:
            with stage_timing("card_source_bots", emit_progress=True):
                started.set()
                assert release.wait(5)
                if fail:
                    raise RuntimeError("private database content")
            source["result_count"] = 2
            return {"private card": {}}

    def records():
        events.flush(force=True)
        return [json.loads(r.message) for r in caplog.records]

    async def run(pool):
        execution = DeadlineExecutor(pool, name="card_metadata", timeout_seconds=0.1)

        async def request():
            with request_budget(5), request_timing("search", "original-trace"):
                return await execution.call(load)

        task = asyncio.create_task(request())
        assert await asyncio.to_thread(started.wait, 2)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
            await task
        before = records()
        assert any(r.get("phase") == "started" for r in before)
        assert not any(r.get("phase") == "finished" for r in before)
        # Another request on the caller loop must not overwrite worker identity.
        with request_budget(5), request_timing("search", "other-trace"):
            pass

    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            asyncio.run(run(pool))
        finally:
            release.set()
    logs = records()
    request = next(
        r
        for r in logs
        if r["event"] == "search_steps" and r["trace_id"] == "original-trace"
    )
    finished = [r for r in logs if r.get("phase") == "finished"]
    assert [r["stage"] for r in finished] == ["card_source_bots", "card_source"]
    for record in finished:
        assert record["request_id"] == request["request_id"] != ""
        assert record["trace_id"] == "original-trace"
        assert record["timestamp"] >= request["timestamp"]
        assert record["elapsed_ms"] > 0
        assert record["outcome"] == ("dependency_error" if fail else "ok")
    if fail:
        assert finished[-1]["error_type"] == "RuntimeError"
    else:
        assert finished[-1]["result_count"] == 2
    assert "private" not in json.dumps(logs)


def test_progress_requires_active_request(caplog):
    events.flush(force=True)
    caplog.clear()
    caplog.set_level(logging.INFO, logger="search_core.events")
    with stage_timing("card_source", emit_progress=True):
        pass
    events.flush(force=True)
    assert not caplog.records
