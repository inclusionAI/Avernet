"""TDD for non-blocking process logging.

Large synchronous log writes must never stall the Uvicorn accept loop. A burst
of diagnostics may be dropped with an explicit counter, but it must not turn
admitted search traffic into kernel-level connection resets.
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def restore_logging_state():
    root = logging.getLogger()
    factory = logging.getLogRecordFactory()
    handlers = root.handlers[:]
    level = root.level
    yield
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers = handlers
    root.setLevel(level)
    logging.setLogRecordFactory(factory)


class SlowHandler(logging.Handler):
    """A handler much slower than a normal stream write."""

    def __init__(self) -> None:
        super().__init__()
        self.records = 0

    def emit(self, record: logging.LogRecord) -> None:
        time.sleep(0.02)
        self.records += 1


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="bcsfuse.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_async_logging_handler_never_blocks_a_logging_burst():
    from app.infra.logging.nonblocking_logging import NonblockingLoggingHandler

    slow = SlowHandler()
    handler = NonblockingLoggingHandler(handlers=[slow], queue_size=10000)
    try:
        started = time.perf_counter()
        for index in range(100):
            handler.emit(_record(f"probe-{index}"))
        elapsed = time.perf_counter() - started
        assert elapsed < 0.1, "emit() must enqueue without doing stream I/O"
        assert handler.dropped_records == 0
        handler.wait(timeout=5.0)
        assert slow.records == 100
    finally:
        handler.close()


def test_queue_overflow_is_explicit_and_does_not_block():
    from app.infra.logging.nonblocking_logging import NonblockingLoggingHandler

    slow = SlowHandler()
    handler = NonblockingLoggingHandler(handlers=[slow], queue_size=2)
    try:
        started = time.perf_counter()
        for index in range(100):
            handler.emit(_record(f"overflow-{index}"))
        elapsed = time.perf_counter() - started
        assert elapsed < 0.1
        assert handler.dropped_records > 0
        assert handler.dropped_records <= 100
    finally:
        handler.close()


def test_public_configure_logging_uses_nonblocking_handler_and_preserves_trace():
    from app.infra.logging.logging_config import configure_logging
    from app.infra.logging.nonblocking_logging import NonblockingLoggingHandler

    output = io.StringIO()
    with patch.dict(os.environ, {"LOG_FORMAT": "%(message)s"}, clear=False):
        with patch("sys.stdout", output):
            configure_logging()

    root = logging.getLogger()
    [proxy] = root.handlers
    assert isinstance(proxy, NonblockingLoggingHandler)
    assert proxy.formatter is not None

    logger = logging.getLogger("app.adapters.http.nonblocking_probe")
    logger.info("pipeline-async-summary")
    assert isinstance(logger.handlers, list)
    assert proxy.dropped_records == 0

    deadline = time.monotonic() + 2.0
    while "pipeline-async-summary" not in output.getvalue() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "pipeline-async-summary" in output.getvalue()
