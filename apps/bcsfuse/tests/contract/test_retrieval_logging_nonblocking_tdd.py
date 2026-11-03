"""TDD: retrieval stage JSON must not be serialized on the request event loop."""

from __future__ import annotations

import io
import logging
import threading

import pytest


@pytest.mark.asyncio
async def test_log_stage_serializes_json_in_the_nonblocking_writer_thread(monkeypatch):
    from app.domain.services import retrieval_logging
    from app.infra.logging.nonblocking_logging import NonblockingLoggingHandler

    serialization_threads: list[threading.Thread] = []

    def recording_dumps(*args, **kwargs):
        serialization_threads.append(threading.current_thread())
        return original_dumps(*args, **kwargs)

    original_dumps = retrieval_logging.json.dumps
    monkeypatch.setattr(
        retrieval_logging.json,
        "dumps",
        lambda *args, **kwargs: recording_dumps(*args, **kwargs),
    )

    logger = logging.getLogger("test_retrieval_nonblocking_json")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    destination = logging.StreamHandler(io.StringIO())
    destination.setFormatter(logging.Formatter("%(message)s"))
    handler = NonblockingLoggingHandler(
        handlers=[destination], queue_size=100, poll_interval_seconds=0.005
    )
    logger.addHandler(handler)
    try:
        retrieval_logging.log_stage(
            logger, "filters", duration_ms=12, result_count=3
        )
        handler.wait(timeout=2)
    finally:
        logger.removeHandler(handler)
        handler.close()

    assert serialization_threads[0] is not threading.main_thread()
    text = destination.stream.getvalue()
    assert "[RETRIEVAL] stage=filters" in text
    assert '"duration_ms": 12' in text
    assert '"result_count": 3' in text
